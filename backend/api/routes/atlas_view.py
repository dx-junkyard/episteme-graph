"""分野の地図 — Atlas API (Issue E-3)。

仕様書 §11 の閲覧系エンドポイント:

- GET /api/atlas?cartridge={id}&level={1|2|3}&focus={node_id?}
- GET /api/atlas/node/{node_id}?cartridge={id}
- POST /api/atlas/report は Issue D 実装済み (routes/atlas.py の report_router)

設計上の重要事項:

- 骨格 + `atlas_overlay_cache` はそのまま返し、個人層 (me: いまここ・足跡・
  隣接の光) のみ実行時合成する。リアルタイム LLM 生成はしない。
- 状態判定ロジックはサーバ側 (core/atlas_state.py)。フロントは描画のみ。
- 応答は Issue B/C のフィクスチャ (`window.ATLAS_FIXTURE`) と互換の形
  (crumbs / initial_selection / nodes / levels) + §11 のトップレベル配列
  (regions / nodes_list / edges / me) を併せ持つ。フロントの差し替えは
  設定切替 (atlas-data.js) だけで済む。
- キャッシュヒット時の L1 応答目標: p95 < 300ms。キャッシュ陳腐化は
  非同期リフレッシュに回し、応答パスでは再計算しない (cold start のみ同期)。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

from core import atlas as atlas_module
from core import atlas_state
from core import atlas_placement
from core import cartridges as cartridges_module
from dependencies import _get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/atlas", tags=["Atlas"])

# フィクスチャと同じ viewBox (§4.1)
_VIEWBOX = {1: (680, 370), 2: (680, 330), 3: (680, 400)}

# §13 ノード数の上限目安 (超過時は代表選出。全件表示はしない)
_MAX_L2_NODES = 20


def _load_learner_skeleton(cartridge_id: str) -> atlas_module.AtlasSkeleton:
    try:
        cartridge = cartridges_module.load_cartridge(cartridge_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"cartridge '{cartridge_id}' not found") from exc
    except ValueError as exc:
        logger.error("invalid bundled atlas skeleton for %s: %s", cartridge_id, exc)
        raise HTTPException(status_code=404, detail="atlas skeleton not available") from exc
    skeleton = cartridge.learner_atlas_skeleton
    if skeleton is None:
        raise HTTPException(status_code=404, detail="atlas skeleton not available")
    return skeleton


def _session():
    try:
        from core.postgres import get_session

        return get_session()
    except Exception:  # noqa: BLE001
        logger.warning("atlas view DB session unavailable", exc_info=True)
        return None


def _ensure_overlay_rows(session, skeleton, cartridge_id: str) -> list[dict]:
    """キャッシュ読み出し。cold start のみ同期リフレッシュ、陳腐化は非同期に回す。"""
    if session is None:
        return []
    rows = atlas_state.fetch_overlay_rows(session, cartridge_id, skeleton.version)
    if not rows:
        try:
            atlas_state.refresh_overlay_cache(session, skeleton, cartridge_id=cartridge_id)
            session.commit()
            rows = atlas_state.fetch_overlay_rows(session, cartridge_id, skeleton.version)
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.warning("atlas cold refresh failed for %s", cartridge_id, exc_info=True)
            return []
        return rows
    try:
        if atlas_state.is_overlay_stale(session, cartridge_id, skeleton.version):
            atlas_state.schedule_overlay_refresh(cartridge_id)
    except Exception:  # noqa: BLE001
        logger.warning("atlas staleness check failed for %s", cartridge_id, exc_info=True)
    return rows


def _personal_layer(session, user_id: str, known_ids: set[str]) -> dict:
    """個人層の合成: いまここ・足跡 (interest_traces の atlas 痕跡)。"""
    if session is None or not user_id:
        return {"now": None, "footprints": [], "visited": []}
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT payload->'atlas'->>'node_id' AS node_id,
                       max(last_seen_at) AS last_seen,
                       min(created_at) AS first_seen
                  FROM interest_traces
                 WHERE user_id = CAST(:uid AS uuid)
                   AND payload->'atlas'->>'node_id' IS NOT NULL
                 GROUP BY 1
                """
            ),
            {"uid": user_id},
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("atlas personal layer query failed", exc_info=True)
        return {"now": None, "footprints": [], "visited": []}

    visited = [
        (str(r[0]), r[1], r[2])
        for r in rows
        if r[0] and str(r[0]) in known_ids
    ]
    if not visited:
        return {"now": None, "footprints": [], "visited": []}
    now_id = max(visited, key=lambda v: (str(v[1]), v[0]))[0]
    footprints = [v[0] for v in sorted(visited, key=lambda v: (str(v[2]), v[0]))]
    return {"now": now_id, "footprints": footprints, "visited": [v[0] for v in visited]}


def _scale(value: float, size: int) -> int:
    return int(round(float(value) * size))


@router.get("")
def get_atlas(
    cartridge: str,
    level: int = 1,
    focus: str | None = None,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """地図データ一式 (骨格 + キャッシュ + 個人層)。

    骨格・状態はキャッシュそのまま、`me` (いまここ・足跡・隣接の光) のみ実行時合成。
    `focus` 指定時は初期選択ノードとして返す (トピック完了直後の導線用)。
    """
    if level not in (1, 2, 3):
        raise HTTPException(status_code=422, detail="level は 1..3 を指定してください")
    skeleton = _load_learner_skeleton(cartridge)
    session = _session()
    try:
        overlay = _ensure_overlay_rows(session, skeleton, cartridge)
        node_rows = {r["entry_id"]: r for r in overlay if r["entry_type"] == "node"}
        region_rows = {r["entry_id"]: r for r in overlay if r["entry_type"] == "region"}
        chain_row = next((r for r in overlay if r["entry_type"] == "chain"), None)

        me = _personal_layer(
            session, str(current_user.get("id") or ""), set(node_rows.keys())
        )
    finally:
        if session is not None:
            session.close()

    concept_region = {
        c.id: r.id for r in skeleton.regions for c in r.concepts
    }
    # 概念間エッジ (L2 / 隣接の光の判定に使う)
    concept_edges = [
        (e.from_id, e.to_id)
        for e in skeleton.edges
        if e.from_id in concept_region and e.to_id in concept_region
    ]
    adjacency: dict[str, set[str]] = {}
    for a, b in concept_edges:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    visited = set(me["visited"])
    now_id = me["now"]
    glow = sorted(
        n for n in adjacency.get(now_id, set())
        if n not in visited and n in node_rows
    ) if now_id else []

    # --- nodes 辞書 (フィクスチャ互換) + 個人層の合成 ---
    nodes: dict[str, dict] = {}
    nodes_list: list[dict] = []
    for entry_id, row in node_rows.items():
        ledger_status = row["status"]
        display_status = ledger_status
        pill = atlas_state.PILL_LABELS.get(ledger_status, ledger_status)
        verify = row["verify_line"]
        if now_id == entry_id:
            display_status = "now"
            pill = atlas_state.PILL_LABELS["now"]
            verify = verify + "あなたの学習の現在地。"
        elif entry_id not in visited and ledger_status in (
            atlas_state.STATUS_VERIFIED,
            atlas_state.STATUS_UNKNOWN,
        ):
            # §10: unvisited は個人の訪問状態 (認識的状態と直交)。表示はグレーに
            # 減衰させるが、検証行には台帳状態をそのまま残す。
            display_status = "unvisited"
            pill = atlas_state.PILL_LABELS["unvisited"]
        node_view = {
            "label": row["label"],
            "pill": pill,
            "status": display_status,
            "ledger_status": ledger_status,
            "verify": verify,
            "endorse": row["endorse_line"],
            "learn": row["learn_enabled"],
            "evid": row["evid_enabled"],
        }
        if entry_id in glow:
            node_view["glow"] = True
        nodes[entry_id] = node_view
        nodes_list.append(
            {
                "id": entry_id,
                "label": row["label"],
                "layout": row["layout"],
                "status": display_status,
                "ledger_status": ledger_status,
                "region_id": row["region_id"],
                "personal": {"visited": entry_id in visited},
                "panel": {
                    "verify": verify,
                    "endorse": row["endorse_line"],
                    "actions": {"learn": row["learn_enabled"], "evid": row["evid_enabled"]},
                },
            }
        )

    # --- L1 (分野レベル) ---
    w1, h1 = _VIEWBOX[1]
    l1_regions = []
    l1_nodes = []
    regions_out = []
    for region in skeleton.regions:
        row = region_rows.get(region.id)
        status = row["status"] if row else atlas_state.STATUS_FOG
        layout = (
            {"x": region.layout.x, "y": region.layout.y, "w": region.layout.w, "h": region.layout.h}
            if region.layout is not None
            else {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        )
        regions_out.append(
            {
                "id": region.id,
                "label": region.label,
                "status": status,
                "layout": layout,
                "verify": row["verify_line"] if row else atlas_state.FOG_VERIFY_LINE,
                "endorse": row["endorse_line"] if row else atlas_state.FOG_ENDORSE_LINE,
            }
        )
        region_box = {
            "id": region.id,
            "label": region.label,
            "kind": "fog" if status == atlas_state.STATUS_FOG else "lit",
            "x": _scale(layout["x"], w1),
            "y": _scale(layout["y"], h1),
            "w": _scale(layout["w"], w1),
            "h": _scale(layout["h"], h1),
        }
        if status == atlas_state.STATUS_FOG:
            dots = atlas_placement.fog_dots(
                region.id, layout, skeleton_version=skeleton.version
            )
            region_box["dots"] = [[_scale(x, w1), _scale(y, h1)] for x, y in dots]
            # 霧領域も詳細パネルで選べるようにノード辞書へ載せる (概念ラベルは持たない)
            nodes.setdefault(
                region.id,
                {
                    "label": f"{region.label}（霧の領域）",
                    "pill": atlas_state.PILL_LABELS[atlas_state.STATUS_FOG],
                    "status": atlas_state.STATUS_FOG,
                    "ledger_status": atlas_state.STATUS_FOG,
                    "verify": atlas_state.FOG_VERIFY_LINE,
                    "endorse": atlas_state.FOG_ENDORSE_LINE,
                    "learn": False,
                    "evid": False,
                },
            )
        l1_regions.append(region_box)
        if status != atlas_state.STATUS_FOG:
            for concept in region.concepts:
                if concept.layout is None or concept.id not in node_rows:
                    continue
                l1_nodes.append(
                    {
                        "id": concept.id,
                        "region": region.id,
                        "x": _scale(concept.layout.x, w1),
                        "y": _scale(concept.layout.y, h1),
                    }
                )

    footprints_l1 = [n for n in me["footprints"] if any(x["id"] == n for x in l1_nodes)]
    leaders = []
    if now_id:
        for a, b in concept_edges:
            if now_id in (a, b) and concept_region.get(a) != concept_region.get(b):
                if any(x["id"] == a for x in l1_nodes) and any(x["id"] == b for x in l1_nodes):
                    pair = [a, b] if a == now_id else [b, a]
                    leaders.append(pair)

    # --- L2 (コースレベル / 概念マップ) ---
    w2, h2 = _VIEWBOX[2]
    l2_nodes = []
    skeleton_concept_ids = set(concept_region.keys())
    for region in skeleton.regions:
        for concept in region.concepts:
            if concept.layout is None or concept.id not in node_rows:
                continue
            l2_nodes.append(
                {
                    "id": concept.id,
                    "x": _scale(concept.layout.x, w2),
                    "y": _scale(concept.layout.y, h2),
                }
            )
    # 配置済みコーパス概念 (E-2)。上限 §13 (L2 ≤ 20)。骨格を優先し、
    # コーパス概念は割当距離の近い順に代表選出する。
    corpus_rows = sorted(
        (
            r
            for r in node_rows.values()
            if r["placement"].get("method") in ("embedding", "binding")
            and r["region_id"]
            and r["layout"]
        ),
        key=lambda r: (r["placement"].get("distance") or 0.0, r["entry_id"]),
    )
    for row in corpus_rows[: max(0, _MAX_L2_NODES - len(l2_nodes))]:
        l2_nodes.append(
            {
                "id": row["entry_id"],
                "x": _scale(row["layout"].get("x", 0.5), w2),
                "y": _scale(row["layout"].get("y", 0.5), h2),
            }
        )
    l2_edges = [[a, b] for a, b in concept_edges]

    # --- L3 (導出レベル) ---
    chain = list((chain_row or {}).get("evidence", {}).get("chain") or [])

    # --- 初期選択 (§4.3: L1/L2 = いまここ、L3 = 行間ステップ) ---
    default_gap = next(
        (nid for nid in chain if nodes.get(nid, {}).get("ledger_status") == atlas_state.STATUS_GAP),
        chain[0] if chain else None,
    )
    focus_id = focus if focus and focus in nodes else None
    initial_selection = {
        "1": focus_id or now_id,
        "2": focus_id or now_id,
        "3": focus_id if focus_id in chain else default_gap,
    }

    cartridge_label = skeleton.cartridge or cartridge
    crumbs = {
        "1": f"{cartridge_label} › 全体　—　ノードを選ぶと下に詳細",
        "2": f"{cartridge_label} › コース（概念マップ）",
        "3": "… › 導出チェーン",
    }

    return {
        "skeleton_version": skeleton.version,
        "cartridge": cartridge,
        "provenance": "AI生成・教員レビュー済",
        "level": level,
        "crumbs": crumbs,
        "initial_selection": initial_selection,
        "nodes": nodes,
        "levels": {
            "1": {
                "viewBox": list(_VIEWBOX[1]),
                "svgTitle": "分野レベル",
                "svgDesc": "灯りの領域と霧の領域、状態つきノード",
                "regions": l1_regions,
                "nodes": l1_nodes,
                "footprints": footprints_l1,
                "leaders": leaders,
            },
            "2": {
                "viewBox": list(_VIEWBOX[2]),
                "svgTitle": "コースレベル",
                "svgDesc": "骨格とコーパス概念を合成した概念マップ",
                "nodes": l2_nodes,
                "edges": l2_edges,
            },
            "3": {
                "viewBox": list(_VIEWBOX[3]),
                "svgTitle": "導出レベル",
                "svgDesc": "理論操作グラフの導出チェーン",
                "chain": chain,
            },
        },
        # §11 のトップレベル配列 (構造化クライアント用。フィクスチャ互換キーとは併存)
        "regions": regions_out,
        "nodes_list": nodes_list,
        "edges": [
            {"from": e.from_id, "to": e.to_id, "kind": e.kind} for e in skeleton.edges
        ],
        "me": {
            "now": now_id,
            "footprints": me["footprints"],
            "glow": glow,
            "unvisited": sorted(set(node_rows.keys()) - visited),
        },
    }


@router.get("/node/{node_id}")
def get_atlas_node(
    node_id: str,
    cartridge: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """詳細パネル用のノード情報 (§11)。"""
    skeleton = _load_learner_skeleton(cartridge)
    session = _session()
    try:
        rows = atlas_state.fetch_overlay_rows(session, cartridge, skeleton.version) if session else []
    finally:
        if session is not None:
            session.close()
    row = next(
        (r for r in rows if r["entry_type"] in ("node", "region") and r["entry_id"] == node_id),
        None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="atlas node not found")
    evidence = row["evidence"] or {}
    endorsements = []
    if evidence.get("endorser_count"):
        endorsements.append(
            {
                "endorser_count": evidence.get("endorser_count", 0),
                "strong_count": evidence.get("strong_count", 0),
                "expertise_breadth": evidence.get("expertise_breadth", 0),
            }
        )
    return {
        "node_id": node_id,
        "label": row["label"],
        "status": row["status"],
        "verify_line": row["verify_line"],
        "endorse_line": row["endorse_line"],
        "evidence_refs": list(evidence.get("evidence_refs") or []),
        "endorsements": endorsements,
        "actions": {"learn": row["learn_enabled"], "evid": row["evid_enabled"]},
        "skeleton_version": skeleton.version,
    }
