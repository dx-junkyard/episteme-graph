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
from core.course_data import course_cartridge_id, course_topics
from dependencies import _get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/atlas", tags=["Atlas"])

# フィクスチャと同じ viewBox (§4.1)
_VIEWBOX = {1: (680, 370), 2: (680, 330), 3: (680, 400)}

# §13 ノード数の上限目安 (超過時は代表選出。全件表示はしない)
_MAX_L2_NODES = 20


def _load_learner_skeleton(cartridge_id: str, session=None) -> atlas_module.AtlasSkeleton:
    """骨格の読み取り (migration 027: DB 凍結版が正本・同梱ファイルはフォールバック)。"""
    from core import atlas_store

    skeleton = atlas_store.load_learner_skeleton(cartridge_id, session)
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


def _concept_abs(x: float, y: float, region_layout: dict) -> tuple[float, float]:
    """骨格概念の領域内相対座標 (0-1) をキャンバス絶対正規化座標へ変換する。

    骨格の ``concept.layout.{x,y}`` は「所属領域ボックス内の相対座標」という規約で、
    seed の ``atlas/skeleton.yaml`` / ``atlas-fixture.js`` / 教員プレビュー
    ``atlas-draft-preview.js`` の ``conceptAbs()`` はいずれもこの相対解釈で一致する。
    L1/L2 の描画・``nodes_list`` はキャンバス絶対座標を前提とするため、ここで領域の
    offset/size を適用して絶対化する。コーパス概念 (``atlas_placement.layout_in_region``
    由来) は既に絶対座標なので、この変換は骨格概念 (placement.method == "skeleton")
    にのみ適用する。
    """
    rx = float(region_layout.get("x", 0.0))
    ry = float(region_layout.get("y", 0.0))
    rw = float(region_layout.get("w", 1.0))
    rh = float(region_layout.get("h", 1.0))
    return rx + x * rw, ry + y * rh


@router.get("/runtime-config")
def atlas_runtime_config() -> dict:
    """フロント (atlas-data.js) のデータソース既定を返す (gap4)。

    既定は "api" (本番でモック地図が全ユーザーに出るのを構造的に防ぐ)。
    開発でフィクスチャを使う場合のみ ATLAS_DATA_SOURCE=fixture を設定する。
    軽量フラグのため認証は要求しない。
    """
    source = "api"
    try:
        from core.config import get_settings

        source = (get_settings().atlas_data_source or "api").strip().lower()
    except Exception:  # noqa: BLE001
        source = "api"
    if source not in ("api", "fixture"):
        source = "api"
    return {"data_source": source}


@router.get("")
def get_atlas(
    cartridge: str | None = None,
    course: str | None = None,
    topic: str | None = None,
    level: int = 1,
    focus: str | None = None,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """地図データ一式 (骨格 + キャッシュ + 個人層)。

    骨格・状態はキャッシュそのまま、`me` (いまここ・足跡・隣接の光) のみ実行時合成。
    - `course` 指定時: コースからカートリッジを導出する (gap3)。`cartridge` は任意。
    - `topic` 指定時: トピックを骨格概念へ対応付けて初期選択 (focus) をサーバ側で解決する
      (gap2)。`focus` (node id) の明示指定が最優先。
    """
    if level not in (1, 2, 3):
        raise HTTPException(status_code=422, detail="level は 1..3 を指定してください")
    session = _session()
    resolved_focus = focus
    relation_threads: dict | None = None  # 推定の糸 (RE層 §6)。既定は「キーなし」
    try:
        # --- カートリッジ決定 (course 指定時はコースから導出。gap3) ---
        cartridge_id = (cartridge or "").strip()
        course_data = None
        derived_cartridge = False  # 明示指定なしの導出 (妥当性ゲートの対象)
        if course:
            try:
                # 注意: コンテナは /app 直下のフラット配置 (`api` パッケージは存在しない)。
                # 他ルーター同様にトップレベル `services` を使う (`from api import services`
                # はテストでは通るが実コンテナで ModuleNotFoundError になる)。
                import services

                course_data = services.get_course_data(
                    str(current_user.get("id") or ""), course
                )
            except Exception:  # noqa: BLE001
                logger.warning("atlas course lookup failed for %s", course, exc_info=True)
                course_data = None
            if course_data is not None:
                resolved = atlas_state.resolve_course_cartridge(session, course_data)
                explicit_course_cartridge = bool(course_cartridge_id(course_data))
                derived_cartridge = (
                    not cartridge_id and not explicit_course_cartridge and bool(resolved)
                )
                cartridge_id = resolved or cartridge_id
        if not cartridge_id:
            raise HTTPException(
                status_code=422, detail="cartridge または course を指定してください"
            )

        skeleton = _load_learner_skeleton(cartridge_id, session)

        # --- 導出カートリッジの妥当性ゲート (gap3 hardening) ---
        # 解析パイプラインは既定カートリッジで走るため、導出だけでは別分野のコースにも
        # 既定カートリッジの地図が出る。コースが骨格へ1つも足がかり (topic→概念対応) を
        # 持たない場合は骨格なしと同じ 404 に縮退させる (フロントは地図領域ごと非表示)。
        if derived_cartridge and not atlas_state.course_has_skeleton_anchor(
            session, skeleton, cartridge_id, course_data
        ):
            raise HTTPException(
                status_code=404, detail="atlas skeleton not available for this course"
            )
        overlay = _ensure_overlay_rows(session, skeleton, cartridge_id)
        node_rows = {r["entry_id"]: r for r in overlay if r["entry_type"] == "node"}
        region_rows = {r["entry_id"]: r for r in overlay if r["entry_type"] == "region"}
        chain_row = next((r for r in overlay if r["entry_type"] == "chain"), None)

        me = _personal_layer(
            session, str(current_user.get("id") or ""), set(node_rows.keys())
        )

        # --- topic → focus 概念のサーバ側解決 (gap2)。focus 明示が最優先 ---
        if not resolved_focus and topic:
            topic_info = None
            if course_data is not None:
                for t in course_topics(course_data):
                    if isinstance(t, dict) and str(t.get("id") or "") == str(topic):
                        topic_info = t
                        break
            # course_data 無し / 未一致でもラベルのみで縮退解決する
            if topic_info is None:
                topic_info = {"title": topic}
            bound = None
            if course_data is not None:
                bound = atlas_state.resolve_topic_concept_via_corpus(
                    session, skeleton, cartridge_id, topic_info
                )
            resolved_focus = atlas_module.match_topic_to_concept(
                topic_info, skeleton, bound_concept_id=bound
            )

        # --- 推定の糸 (RE層 §6。optional トップレベルキー threads) ---
        # 付加物なので、導出できないときはキーごと落とす (fail-soft = RE2/RE6)。
        # セッションが要るので取得はここで行い、レスポンスへのマージは組み立て後に行う。
        if session is not None:
            try:
                from core.atlas_edges.threads import threads_for_domain

                relation_threads = threads_for_domain(session, cartridge_id)
            except Exception:  # noqa: BLE001 — 糸の失敗で地図を壊さない
                logger.warning(
                    "atlas relation threads unavailable for %s (non-fatal)",
                    cartridge_id,
                    exc_info=True,
                )
                relation_threads = None
    finally:
        if session is not None:
            session.close()

    concept_region = {
        c.id: r.id for r in skeleton.regions for c in r.concepts
    }
    # 領域ボックス (キャンバス絶対 0-1)。骨格概念の相対座標を絶対化するのに使う。
    region_layout_by_id = {
        r.id: (
            {"x": r.layout.x, "y": r.layout.y, "w": r.layout.w, "h": r.layout.h}
            if r.layout is not None
            else {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
        )
        for r in skeleton.regions
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
        # 骨格概念の layout は領域内相対。nodes_list はキャンバス絶対座標を前提と
        # するため絶対化する (コーパス概念は既に絶対なのでそのまま)。
        raw_layout = row["layout"] or {}
        if row.get("placement", {}).get("method") == "skeleton" and raw_layout:
            ax, ay = _concept_abs(
                float(raw_layout.get("x", 0.5)),
                float(raw_layout.get("y", 0.5)),
                region_layout_by_id.get(row["region_id"], {}),
            )
            layout_out = {"x": ax, "y": ay}
        else:
            layout_out = raw_layout
        nodes_list.append(
            {
                "id": entry_id,
                "label": row["label"],
                "layout": layout_out,
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
                # 骨格概念の layout は領域内相対 → 領域 box を適用して絶対化する
                ax, ay = _concept_abs(concept.layout.x, concept.layout.y, layout)
                l1_nodes.append(
                    {
                        "id": concept.id,
                        "region": region.id,
                        "x": _scale(ax, w1),
                        "y": _scale(ay, h1),
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
        region_layout = region_layout_by_id.get(region.id, {})
        for concept in region.concepts:
            if concept.layout is None or concept.id not in node_rows:
                continue
            # 骨格概念の layout は領域内相対 → 領域 box を適用して絶対化する
            ax, ay = _concept_abs(concept.layout.x, concept.layout.y, region_layout)
            l2_nodes.append(
                {
                    "id": concept.id,
                    "x": _scale(ax, w2),
                    "y": _scale(ay, h2),
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
    focus_id = resolved_focus if resolved_focus and resolved_focus in nodes else None
    initial_selection = {
        "1": focus_id or now_id,
        "2": focus_id or now_id,
        "3": focus_id if focus_id in chain else default_gap,
    }

    cartridge_label = skeleton.cartridge or cartridge_id
    crumbs = {
        "1": f"{cartridge_label} › 全体　—　ノードを選ぶと下に詳細",
        "2": f"{cartridge_label} › コース（概念マップ）",
        "3": "… › 導出チェーン",
    }

    payload: dict = {
        "skeleton_version": skeleton.version,
        "cartridge": cartridge_id,
        "provenance": "AI生成・教員レビュー済",
        "level": level,
        "focus": focus_id,
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
    # 推定の糸は「導出できたときだけ」載せる。available が偽ならキー自体を付けない
    # (フロントはコントロールごと非表示 = RE2 の fail-closed)。
    if isinstance(relation_threads, dict) and relation_threads.get("available"):
        payload["threads"] = relation_threads
    return payload


@router.get("/node/{node_id}")
def get_atlas_node(
    node_id: str,
    cartridge: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """詳細パネル用のノード情報 (§11)。"""
    session = _session()
    try:
        skeleton = _load_learner_skeleton(cartridge, session)
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
