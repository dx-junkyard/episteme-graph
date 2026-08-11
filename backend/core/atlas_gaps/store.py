"""カテゴリギャップ候補 — ``landscape_gap_signals`` / ``atlas_gap_decisions``
（migration 066）の DB プリミティブ。

正本: ``docs/features/category_gap_candidates_design.md`` §5.2 / §5.3。

``core/landscape/store.py`` / ``core/atlas_store.py`` と同じ流儀:

- セッションは **呼び出し側が管理する**（``core/postgres.get_session()`` + try/finally）。
  本モジュールは commit / rollback / close を行わない — 呼び出し側（パイプラインの
  builder・API 層）が1トランザクションとして束ねる。
- 第1引数は必ず ``session``。SQL は ``sqlalchemy.text`` で書き、ORM を使わない。
- FastAPI / services / LLM SDK に依存しない（開発ルール2）。
- バインドパラメータの型キャストは ``CAST(:x AS uuid)`` の形で書く（バインド名の
  直後にコロン2つを続ける PostgreSQL のキャスト記法は、SQLAlchemy がバインドを
  検出できず literal のまま送られる既知の罠）。

不変条項（設計書 §2）:

- **2層分離（§4.3 裁定）**: 論文単位の**信号**だけを行として持ち、cluster 単位の
  **候補**は :func:`derive_candidates` が毎回導出する（G1 / PN-2: 導出であって記録では
  ない）。行として持つのは教員の**判断**（``atlas_gap_decisions``）だけ。
- **LS3 同型の再解析セマンティクス**: :func:`record_signals` は当該 document の
  ``status='active'`` 行を ``superseded`` にしてから挿入する。**空入力では SQL を
  一切発行しない**（0件の解析が生きた信号を消さない）。
- **P4 / AB3 情報を落とさない**: 本モジュールに DELETE 文は無い（行削除 API を
  作らない）。見送りは ``status='dismissed'``、その取り消しは ``'candidate'`` への
  遷移で表す。孤児掃除は ``documents(id) ON DELETE CASCADE``（migration 066）に委ねる。
- **LS5 数値を見せない**: ``confidence`` は行 dict（DB 界面）までで、
  :func:`derive_candidates` は段階ラベルしか載せない。支持論文も件数ではなく
  リストで返す（「該当論文 N 件」を作らない）。
- **LS7 地図の安定性**: 本モジュールは ``atlas_skeletons`` へ一切書き込まない
  （骨格の変更は draft→freeze の既存フローのみ。ガードレールが構造的に検査する）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Mapping

from sqlalchemy import text as sa_text

from core import atlas as atlas_module
from core.atlas_gaps import schema
from core.schema import AUDIT_ENTITY_CATEGORY_GAP

logger = logging.getLogger(__name__)

_SIGNAL_COLUMNS_SQL = """
    s.id::text, s.document_id::text, s.run_id::text, s.domain_key, s.skeleton_version,
    s.layer, s.parent_region_id, s.proposed_label, s.normalized_label, s.reason,
    s.evidence_quote, s.confidence, s.status, s.created_at, s.updated_at
"""

_DECISION_COLUMNS_SQL = """
    id::text, cluster_key, status, review_note, merged_into, draft_node_id,
    applied_version, decided_by::text, decided_at, created_at, updated_at
"""


# ---------------------------------------------------------------------------
# 変換ヘルパ
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """agent 出力（dataclass）と dict の両方から同じキーを読む防御アクセス。

    並行実装中の agent 契約（``CategoryGapRecord``）が dataclass でも dict でも
    壊れないようにする（builder 側の ``getattr(result, "category_gaps", [])`` と同じ思想）。
    """
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _signal_row_to_dict(row: Any, *, title: str | None = None) -> dict:
    """信号の DB 行 → dict（列順は :data:`_SIGNAL_COLUMNS_SQL` と一致させること）。

    ``confidence`` は DB 界面としてそのまま載せる（LS5 の適用点は DTO 側 =
    :func:`derive_candidates` / route の投影）。
    """
    out = {
        "id": str(row[0]),
        "document_id": str(row[1]) if row[1] is not None else "",
        "run_id": row[2] or None,
        "domain_key": row[3] or "",
        "skeleton_version": row[4] or "",
        "layer": row[5] or schema.GAP_LAYER_CONCEPT,
        "parent_region_id": row[6] or "",
        "proposed_label": row[7] or "",
        "normalized_label": row[8] or "",
        "reason": row[9] or "",
        "evidence_quote": row[10] or "",
        "confidence": float(row[11]) if row[11] is not None else None,
        "status": row[12] or schema.SIGNAL_STATUS_ACTIVE,
        "created_at": _iso(row[13]) or "",
        "updated_at": _iso(row[14]) or "",
    }
    if title is not None:
        out["document_title"] = title
    return out


def _decision_row_to_dict(row: Any) -> dict:
    """判断の DB 行 → dict（列順は :data:`_DECISION_COLUMNS_SQL` と一致させること）。"""
    return {
        "id": str(row[0]),
        "cluster_key": row[1] or "",
        "status": row[2] or schema.DECISION_STATUS_CANDIDATE,
        "review_note": row[3] or "",
        "merged_into": row[4] or "",
        "draft_node_id": row[5] or "",
        "applied_version": row[6] or "",
        "decided_by": row[7] or None,
        "decided_at": _iso(row[8]),
        "created_at": _iso(row[9]) or "",
        "updated_at": _iso(row[10]) or "",
        "status_label": schema.decision_status_label(
            row[2] or schema.DECISION_STATUS_CANDIDATE
        ),
    }


# ---------------------------------------------------------------------------
# 信号の投入（パイプライン相乗り。§5.1 / LS3 同型の再解析セマンティクス）
# ---------------------------------------------------------------------------


def _normalize_gap(raw: Any, skeleton_versions: Mapping[str, str] | None) -> dict | None:
    """agent 出力の1件を DB 行の形へ正規化する（不正は ``None`` = その1件のみ drop）。

    ここが ``core/landscape/store.py::_validate_candidate`` と**意図的に違う**点:
    あちらは語彙違反を ``ValueError`` で fail-closed にするが、こちらは 1 件だけ落として
    処理を続ける。gap 信号は配置（placements）の**付随情報**であり、同一トランザクション
    で保存されるため、gap のノイズ 1 件で配置の保存まで巻き戻してはならない
    （設計書 §5.1 の「soft collector」を DB 界面でも維持する）。落とした件は必ず
    warning ログに残す（黙って消さない）。
    """
    domain_key = _clean(_get(raw, "domain_key"))
    proposed_label = _clean(_get(raw, "proposed_label"))
    layer = _clean(_get(raw, "layer"))
    if not domain_key or not proposed_label:
        logger.warning(
            "category_gap: dropping a signal without domain_key / proposed_label "
            "(domain_key=%r, proposed_label=%r)",
            domain_key,
            proposed_label,
        )
        return None
    if not schema.is_valid_layer(layer):
        # 語彙外・空の layer は推測で埋めない（region と concept のどちらかを
        # 勝手に決めると存在しない構造を作る）。
        logger.warning(
            "category_gap: dropping a signal with an unknown layer: %r (label=%r)",
            layer,
            proposed_label,
        )
        return None
    normalized_label = schema.normalize_label(proposed_label)
    if not normalized_label:
        logger.warning(
            "category_gap: dropping a signal whose label normalizes to empty: %r",
            proposed_label,
        )
        return None
    parent_region_id = _clean(_get(raw, "parent_region_id"))
    if layer == schema.GAP_LAYER_REGION:
        # 領域候補に親は無い（cluster_key を安定させるため空に正規化する）。
        parent_region_id = ""
    return {
        "domain_key": domain_key,
        "skeleton_version": _clean((skeleton_versions or {}).get(domain_key, "")),
        "layer": layer,
        "parent_region_id": parent_region_id,
        "proposed_label": proposed_label,
        "normalized_label": normalized_label,
        "reason": _clean(_get(raw, "reason")),
        "evidence_quote": _clean(_get(raw, "evidence_quote")),
        "confidence": schema.normalize_confidence(_get(raw, "confidence")),
    }


def record_signals(
    session: Any,
    *,
    document_id: str,
    run_id: str | None,
    gaps: Iterable[Any],
    skeleton_versions: Mapping[str, str] | None = None,
    max_signals: int | None = None,
) -> int:
    """1 document 分のギャップ信号を再解析セーフに投入する（§5.1 / §5.2）。

    手順（``core/landscape/store.py::supersede_and_insert_candidates`` と**同一粒度**):

    1. 当該 document の ``status='active'`` 行を全て ``superseded`` に遷移させる
       （document 単位。教員の判断は別テーブルなので信号側の supersede で消えない）
    2. 正規化を通った信号を ``status='active'`` で挿入する

    ``gaps`` が空・全件 drop のときは **DB を一切触らず** 0 を返す（0件の解析が
    生きた信号を消しに行かない — LS3 の「空 candidates は SQL 非発行」と同じ規則）。

    ``skeleton_versions`` は ``domain_key -> 骨格の凍結版`` の対応（builder が
    ``collect_placement_domains()`` の結果から組む）。刻印であり cluster_key には
    入らない（§4.2 裁定）。

    ``max_signals`` は 1 document あたりの保存上限（既定は
    ``settings.landscape_gap_max_per_document``）。超過分は保存しない（先勝ち）。
    同一 document 内の同一 cluster（domain / layer / 親領域 / 正規化ラベル）は
    先勝ちで1件に畳む。

    戻り値は挿入した件数。
    """
    if max_signals is None:
        max_signals = _default_max_signals()
    limit = max(0, int(max_signals or 0))
    if limit <= 0:
        # 上限 0 = この機能を止める設定。SQL を発行しない（既存の信号にも触らない）。
        return 0

    validated: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in gaps or []:
        if len(validated) >= limit:
            logger.info(
                "category_gap: signal limit (%d) reached for document=%s; "
                "the remaining candidates are not stored",
                limit,
                document_id,
            )
            break
        item = _normalize_gap(raw, skeleton_versions)
        if item is None:
            continue
        key = (
            item["domain_key"],
            item["layer"],
            item["parent_region_id"],
            item["normalized_label"],
        )
        if key in seen:
            continue
        seen.add(key)
        validated.append(item)

    if not validated:
        return 0

    session.execute(
        sa_text(
            """
            UPDATE landscape_gap_signals
               SET status = :superseded, updated_at = now()
             WHERE document_id = CAST(:document_id AS uuid)
               AND status = ANY(:supersedable)
            """
        ),
        {
            "document_id": document_id,
            "superseded": schema.SIGNAL_STATUS_SUPERSEDED,
            "supersedable": list(schema.SUPERSEDABLE_SIGNAL_STATUSES),
        },
    )

    created = 0
    for item in validated:
        session.execute(
            sa_text(
                """
                INSERT INTO landscape_gap_signals (
                    document_id, run_id, domain_key, skeleton_version, layer,
                    parent_region_id, proposed_label, normalized_label, reason,
                    evidence_quote, confidence, status
                ) VALUES (
                    CAST(:document_id AS uuid), CAST(:run_id AS uuid), :domain_key,
                    :skeleton_version, :layer, :parent_region_id, :proposed_label,
                    :normalized_label, :reason, :evidence_quote, :confidence, :status
                )
                """
            ),
            {
                "document_id": document_id,
                "run_id": run_id or None,
                "domain_key": item["domain_key"],
                "skeleton_version": item["skeleton_version"],
                "layer": item["layer"],
                "parent_region_id": item["parent_region_id"],
                "proposed_label": item["proposed_label"],
                "normalized_label": item["normalized_label"],
                "reason": item["reason"],
                "evidence_quote": item["evidence_quote"],
                "confidence": item["confidence"],
                # AI 由来の投入は必ず active（判断は別テーブル・確定は教員のみ）。
                "status": schema.SIGNAL_STATUS_ACTIVE,
            },
        )
        created += 1
    return created


def _default_max_signals() -> int:
    """1 document あたりの信号保存上限（``core.config`` 正本・fail-open）。"""
    try:
        from core.config import get_settings

        return int(getattr(get_settings(), "landscape_gap_max_per_document", 3) or 0)
    except Exception:  # noqa: BLE001 — 設定不達で保存経路を落とさない
        logger.debug("category_gap: failed to read gap limit from settings", exc_info=True)
        return 3


def record_detect_audit(
    session: Any,
    *,
    document_id: str,
    run_id: str | None = None,
    created: int = 0,
    domain_keys: Iterable[str] = (),
) -> None:
    """検出（AI 由来）を ``theory_review_events`` に1 run 1件で記帳する（§5.7）。

    core 層からの直接 INSERT は原則禁止（``services.record_review_event`` に委譲する）
    だが、**呼び出し元のトランザクションに同乗する**ケースは
    ``core/document_pipeline/persistence.py`` と同じ例外として許容されている
    （CLAUDE.md「監査 entity_type カタログ」）。ここは builder の ``_persist``
    トランザクション内で信号の挿入と一体で記帳する必要があるため、その例外に当たる。

    ``changed_by`` は NULL（人間の操作ではない）。metadata の ``action='detect'`` で
    教員の accept / dismiss / restore / merge / incorporate と区別する。
    """
    session.execute(
        sa_text(
            """
            INSERT INTO theory_review_events (
                entity_type, entity_id, old_status, new_status, changed_by, metadata
            )
            VALUES (
                :entity_type, :entity_id, :old_status, :new_status,
                CAST(:changed_by AS uuid), CAST(:metadata AS jsonb)
            )
            """
        ),
        {
            "entity_type": AUDIT_ENTITY_CATEGORY_GAP,
            "entity_id": str(document_id or ""),
            "old_status": "",
            "new_status": schema.DECISION_STATUS_CANDIDATE,
            "changed_by": None,
            "metadata": json.dumps(
                {
                    "action": schema.AUDIT_ACTION_DETECT,
                    "run_id": run_id or None,
                    "created": int(created or 0),
                    "domain_keys": sorted({_clean(d) for d in (domain_keys or []) if _clean(d)}),
                },
                ensure_ascii=False,
            ),
        },
    )


# ---------------------------------------------------------------------------
# 信号の読み出し
# ---------------------------------------------------------------------------


def _fetch_active_signals(
    session: Any,
    *,
    domain_key: str | None = None,
    document_id: str | None = None,
    with_titles: bool = False,
) -> list[dict]:
    clauses = ["s.status = :active"]
    params: dict[str, Any] = {"active": schema.SIGNAL_STATUS_ACTIVE}
    if _clean(domain_key):
        clauses.append("s.domain_key = :domain_key")
        params["domain_key"] = _clean(domain_key)
    if _clean(document_id):
        clauses.append("s.document_id = CAST(:document_id AS uuid)")
        params["document_id"] = _clean(document_id)

    if with_titles:
        sql = f"""
            SELECT {_SIGNAL_COLUMNS_SQL}, COALESCE(d.title, '')
              FROM landscape_gap_signals s
              LEFT JOIN documents d ON d.id = s.document_id
             WHERE {" AND ".join(clauses)}
             ORDER BY s.layer, s.parent_region_id, s.normalized_label,
                      s.created_at DESC, s.id
        """
    else:
        sql = f"""
            SELECT {_SIGNAL_COLUMNS_SQL}
              FROM landscape_gap_signals s
             WHERE {" AND ".join(clauses)}
             ORDER BY s.layer, s.parent_region_id, s.normalized_label,
                      s.created_at DESC, s.id
        """
    rows = session.execute(sa_text(sql), params).fetchall()
    if with_titles:
        return [_signal_row_to_dict(r, title=(r[15] or "")) for r in rows]
    return [_signal_row_to_dict(r) for r in rows]


def list_active_signals(
    session: Any,
    *,
    domain_key: str | None = None,
    document_id: str | None = None,
) -> list[dict]:
    """生きている（``active``）信号の一覧。

    ``domain_key`` / ``document_id`` は任意の絞り込み（どちらも省略すると全件）。
    履歴（``superseded``）は返さない（P4 で行は残るが、既定の読みには出さない）。
    """
    return _fetch_active_signals(
        session, domain_key=domain_key, document_id=document_id
    )


# ---------------------------------------------------------------------------
# 骨格の索引（読み時導出のための突合材料）
# ---------------------------------------------------------------------------


def _skeleton_regions(frozen_skeleton: Any) -> list[Any]:
    """``AtlasSkeleton`` / dict / ``{"atlas_skeleton": {...}}`` から regions を取り出す。"""
    if frozen_skeleton is None:
        return []
    if isinstance(frozen_skeleton, Mapping):
        inner = frozen_skeleton.get("atlas_skeleton")
        if isinstance(inner, Mapping):
            return list(inner.get("regions") or [])
        return list(frozen_skeleton.get("regions") or [])
    return list(getattr(frozen_skeleton, "regions", ()) or ())


def _skeleton_index(frozen_skeleton: Any) -> dict:
    """凍結骨格 → 突合用の索引（純粋・読み取りのみ）。

    - ``resolved``: 全ノード（領域・概念）の正規化ラベルと正規化 id の集合。
      レイヤーで非対称にしない — 同名のノードが地図にある時点で「この地図では
      言い表せなかった主題」ではなくなるため（解消済みとして候補から外す）。
      id は ``_`` を空白に置いた変種も入れる（id は ``_slugify`` が空白を ``_`` に
      した slug なので、``cmb_lensing`` は ``CMB Lensing`` の解消とみなす）。
    - ``region_labels`` / ``region_concept_counts``: 親領域の表示名と概念の詰まり具合。
    """
    resolved: set[str] = set()
    region_labels: dict[str, str] = {}
    region_concept_counts: dict[str, int] = {}

    def _add_id(value: str) -> None:
        if not value:
            return
        resolved.add(schema.normalize_label(value))
        resolved.add(schema.normalize_label(value.replace("_", " ")))

    for region in _skeleton_regions(frozen_skeleton):
        region_id = _clean(_get(region, "id"))
        label = _clean(_get(region, "label"))
        if not region_id:
            continue
        region_labels[region_id] = label or region_id
        concepts = list(_get(region, "concepts") or ())
        region_concept_counts[region_id] = len(concepts)
        resolved.add(schema.normalize_label(label or region_id))
        _add_id(region_id)
        for concept in concepts:
            concept_id = _clean(_get(concept, "id"))
            concept_label = _clean(_get(concept, "label"))
            if concept_label:
                resolved.add(schema.normalize_label(concept_label))
            _add_id(concept_id)
    resolved.discard("")
    return {
        "resolved": resolved,
        "region_labels": region_labels,
        "region_concept_counts": region_concept_counts,
    }


# ---------------------------------------------------------------------------
# 候補の読み時導出（§4.3 裁定: 候補は行として持たない）
# ---------------------------------------------------------------------------


def _fetch_decisions(session: Any, cluster_keys: Iterable[str]) -> dict[str, dict]:
    keys = sorted({_clean(k) for k in (cluster_keys or []) if _clean(k)})
    if not keys:
        return {}
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_DECISION_COLUMNS_SQL} FROM atlas_gap_decisions
             WHERE cluster_key = ANY(:keys)
            """
        ),
        {"keys": keys},
    ).fetchall()
    out: dict[str, dict] = {}
    for row in rows:
        rec = _decision_row_to_dict(row)
        out[rec["cluster_key"]] = rec
    return out


def derive_candidates(
    session: Any,
    *,
    domain_key: str,
    frozen_skeleton: Any = None,
    current_version: str = "",
    include_dismissed: bool = False,
) -> list[dict]:
    """レビューキュー（cluster 単位の候補）を**毎回導出**する（§4.3 / §5.2）。

    手順:

    1. 当該ドメインの ``active`` 信号を cluster_key でグルーピングする
    2. **distinct document が :data:`schema.MIN_DOCUMENTS_FOR_CANDIDATE` 件以上**の
       cluster だけ残す（§4.1 裁定。1論文の主題は分野のカテゴリではない）
    3. 現行凍結骨格に同名のノード（領域・概念いずれか）がある cluster を外す
       （次版で概念が入れば候補は自然消滅する = 完了フラグ不要, G1）
    4. 判断（``atlas_gap_decisions``）を突き合わせ、``dismissed`` / ``merged`` は
       ``include_dismissed=True`` のときだけ含める（却下の永続性・§4.2）

    返す各 cluster は以下を持つ。**``confidence`` の生値は載せない**（段階ラベルのみ。
    LS5）。**支持論文の件数フィールドも作らない**（``documents`` のリストで示す）。

    ``{cluster_key, domain_key, layer, layer_label, parent_region_id,
    parent_region_label, parent_region_known, parent_region_at_capacity,
    proposed_label, normalized_label, documents: [{document_id, title, reason,
    evidence_quote, skeleton_version, version_mismatch, confidence_label,
    created_at}], version_mismatch, decision}``

    ``version_mismatch`` は「旧版の地図に対する信号が含まれる」の事実
    （``atlas_reports.summarize_queue`` と同じ表示語彙）。``current_version`` が
    空のときは判定しない（False）。
    """
    domain = _clean(domain_key)
    if not domain:
        return []

    signals = _fetch_active_signals(session, domain_key=domain, with_titles=True)
    if not signals:
        return []

    index = _skeleton_index(frozen_skeleton)
    resolved: set[str] = index["resolved"]
    region_labels: dict[str, str] = index["region_labels"]
    region_counts: dict[str, int] = index["region_concept_counts"]

    grouped: dict[str, list[dict]] = {}
    for signal in signals:
        cluster_key = schema.build_cluster_key(
            signal["domain_key"], signal["parent_region_id"], signal["proposed_label"]
        )
        grouped.setdefault(cluster_key, []).append(signal)

    decisions = _fetch_decisions(session, grouped.keys())

    version = _clean(current_version)
    out: list[dict] = []
    for cluster_key, members in grouped.items():
        # 同一 document から複数の信号が同じ cluster に入ることは通常無いが
        # （record_signals が畳む）、あっても新しい方を代表にする。
        by_document: dict[str, dict] = {}
        for signal in members:
            document_id = signal["document_id"]
            if document_id not in by_document:
                by_document[document_id] = signal
        if len(by_document) < schema.MIN_DOCUMENTS_FOR_CANDIDATE:
            continue

        representative = members[0]  # created_at DESC 順（最新の表記を代表にする）
        if representative["normalized_label"] in resolved:
            # 現行凍結版に同名のノードがある = 解消済み（自然消滅）
            continue

        decision = decisions.get(cluster_key)
        if (
            decision is not None
            and decision["status"] in schema.SUPPRESSED_DECISION_STATUSES
            and not include_dismissed
        ):
            continue

        layer = representative["layer"]
        parent_region_id = representative["parent_region_id"]
        parent_known = bool(parent_region_id) and parent_region_id in region_labels
        at_capacity = bool(
            layer == schema.GAP_LAYER_CONCEPT
            and parent_known
            and region_counts.get(parent_region_id, 0)
            >= atlas_module.MAX_CONCEPTS_PER_REGION
        )

        documents = []
        for signal in sorted(
            by_document.values(),
            key=lambda s: (s["created_at"], s["id"]),
            reverse=True,
        ):
            documents.append(
                {
                    "document_id": signal["document_id"],
                    "title": signal.get("document_title") or "",
                    "reason": signal["reason"],
                    "evidence_quote": signal["evidence_quote"],
                    "skeleton_version": signal["skeleton_version"],
                    "version_mismatch": bool(
                        version and signal["skeleton_version"] != version
                    ),
                    # LS5: 生値ではなく段階ラベル
                    "confidence_label": schema.confidence_label(signal["confidence"]),
                    "created_at": signal["created_at"],
                }
            )

        out.append(
            {
                "cluster_key": cluster_key,
                "domain_key": domain,
                "layer": layer,
                "layer_label": schema.layer_label(layer),
                "parent_region_id": parent_region_id,
                "parent_region_label": region_labels.get(parent_region_id, ""),
                "parent_region_known": parent_known,
                "parent_region_at_capacity": at_capacity,
                "proposed_label": representative["proposed_label"],
                "normalized_label": representative["normalized_label"],
                "documents": documents,
                "version_mismatch": any(d["version_mismatch"] for d in documents),
                "decision": decision,
            }
        )

    out.sort(key=lambda c: (c["layer"], c["parent_region_id"], c["normalized_label"]))
    return out


# ---------------------------------------------------------------------------
# 教員の判断（確定は人間のみ・KN-3 / AB4）
# ---------------------------------------------------------------------------


def get_decision(session: Any, cluster_key: str) -> dict | None:
    """1 cluster の判断行（無ければ ``None``）。"""
    key = _clean(cluster_key)
    if not key:
        return None
    row = session.execute(
        sa_text(
            f"SELECT {_DECISION_COLUMNS_SQL} FROM atlas_gap_decisions "
            "WHERE cluster_key = :cluster_key"
        ),
        {"cluster_key": key},
    ).fetchone()
    return _decision_row_to_dict(row) if row else None


def upsert_decision(
    session: Any,
    *,
    cluster_key: str,
    status: str,
    decided_by: str,
    review_note: str = "",
    merged_into: str = "",
) -> dict:
    """cluster 単位の教員判断を記録する（``ON CONFLICT (cluster_key)`` で最新に上書き）。

    検証（いずれも ``ValueError``。route が 422 に変換する契約）:

    - ``cluster_key`` が空
    - ``decided_by`` が空（帰属必須・匿名の判断を作らない）
    - ``status`` が :data:`schema.DECISION_STATUSES` の語彙外
    - ``status='dismissed'`` で ``review_note`` が空（見送りは理由必須・§5.4）
    - ``status='merged'`` で ``merged_into`` が空（統合先の無い統合を作らない）

    ``review_note`` / ``merged_into`` が空文字のときは既存の値を保持する（P4:
    状態だけ変えたときに理由文を消さない。``landscape/store.update_status`` と同じ規則）。
    ``draft_node_id`` / ``applied_version`` は**触らない** — どちらも「実際に下書きへ
    入れた / 版に反映された」という履歴の事実で、後の判断変更で消してはならない。
    """
    key = _clean(cluster_key)
    if not key:
        raise ValueError("cluster_key is required")
    if not _clean(decided_by):
        raise ValueError("decided_by is required")
    if not schema.is_valid_decision_status(status):
        raise ValueError(
            f"invalid decision status: {status!r} "
            f"(must be one of {schema.DECISION_STATUSES!r})"
        )
    note = str(review_note or "").strip()
    if status in schema.REVIEW_NOTE_REQUIRED_STATUSES and not note:
        raise ValueError("review_note is required when dismissing a category gap")
    merged = _clean(merged_into)
    if status in schema.MERGED_INTO_REQUIRED_STATUSES and not merged:
        raise ValueError("merged_into is required when merging a category gap")

    row = session.execute(
        sa_text(
            f"""
            INSERT INTO atlas_gap_decisions (
                cluster_key, status, review_note, merged_into, decided_by, decided_at
            ) VALUES (
                :cluster_key, :status, :review_note, :merged_into,
                CAST(:decided_by AS uuid), now()
            )
            ON CONFLICT (cluster_key) DO UPDATE
               SET status = :status,
                   review_note = CASE WHEN :review_note <> ''
                        THEN :review_note ELSE atlas_gap_decisions.review_note END,
                   merged_into = CASE WHEN :merged_into <> ''
                        THEN :merged_into ELSE atlas_gap_decisions.merged_into END,
                   decided_by = CAST(:decided_by AS uuid),
                   decided_at = now(),
                   updated_at = now()
            RETURNING {_DECISION_COLUMNS_SQL}
            """
        ),
        {
            "cluster_key": key,
            "status": status,
            "review_note": note,
            "merged_into": merged,
            "decided_by": _clean(decided_by),
        },
    ).fetchone()
    if row is None:
        raise ValueError("failed to record the category gap decision")
    return _decision_row_to_dict(row)


def restore_decision(session: Any, *, cluster_key: str, decided_by: str) -> dict | None:
    """見送りを取り消して ``candidate`` に戻す（**行削除ではなく状態遷移** — P4 / AB3）。

    ``review_note`` は消さない（なぜ一度見送ったかの履歴を残す）。判断行が無ければ
    ``None``（戻す対象が無い = 呼び出し側は 404）。
    """
    key = _clean(cluster_key)
    if not key:
        return None
    if not _clean(decided_by):
        raise ValueError("decided_by is required")
    row = session.execute(
        sa_text(
            f"""
            UPDATE atlas_gap_decisions
               SET status = :candidate,
                   decided_by = CAST(:decided_by AS uuid),
                   decided_at = now(),
                   updated_at = now()
             WHERE cluster_key = :cluster_key
            RETURNING {_DECISION_COLUMNS_SQL}
            """
        ),
        {
            "cluster_key": key,
            "candidate": schema.DECISION_STATUS_CANDIDATE,
            "decided_by": _clean(decided_by),
        },
    ).fetchone()
    return _decision_row_to_dict(row) if row else None


def mark_incorporated(
    session: Any, *, cluster_key: str, draft_node_id: str
) -> dict | None:
    """採用済み候補に「次版下書きへ入れた node の id」を刻印する（§5.4）。

    ``status='accepted'`` の行だけを対象にする（未採用の候補を下書き取り込みだけで
    採用扱いにしない）。対象が無ければ ``None``（呼び出し側は 404 / 409）。
    ``applied_version`` はここでは触らない — 反映は凍結時
    （:func:`stamp_applied_versions`）に刻印する（採用と反映の分離）。
    """
    key = _clean(cluster_key)
    node_id = _clean(draft_node_id)
    if not key:
        return None
    if not node_id:
        raise ValueError("draft_node_id is required")
    row = session.execute(
        sa_text(
            f"""
            UPDATE atlas_gap_decisions
               SET draft_node_id = :draft_node_id, updated_at = now()
             WHERE cluster_key = :cluster_key AND status = :accepted
            RETURNING {_DECISION_COLUMNS_SQL}
            """
        ),
        {
            "cluster_key": key,
            "draft_node_id": node_id,
            "accepted": schema.DECISION_STATUS_ACCEPTED,
        },
    ).fetchone()
    return _decision_row_to_dict(row) if row else None


def stamp_applied_versions(
    session: Any,
    *,
    domain_key: str,
    frozen_version: str,
    frozen_node_ids: Iterable[str],
) -> list[str]:
    """凍結時に「実際に反映された」判断へ ``applied_version`` を刻印する（§5.4）。

    対象は当該ドメインの ``status='accepted'`` かつ ``applied_version=''`` かつ
    ``draft_node_id`` が**凍結された骨格に実在する**行だけ（採用しただけ・下書きに
    入れただけでは刻印しない）。

    ``frozen_version`` または ``frozen_node_ids`` が空なら **SQL を発行せず** ``[]``
    （空集合を「全件」に転ばせない fail-closed）。戻り値は刻印した cluster_key の一覧。
    """
    domain = _clean(domain_key)
    version = _clean(frozen_version)
    node_ids = sorted({_clean(n) for n in (frozen_node_ids or []) if _clean(n)})
    if not domain or not version or not node_ids:
        return []
    rows = session.execute(
        sa_text(
            """
            UPDATE atlas_gap_decisions
               SET applied_version = :version, updated_at = now()
             WHERE starts_with(cluster_key, :domain_prefix)
               AND status = :accepted
               AND applied_version = ''
               AND draft_node_id <> ''
               AND draft_node_id = ANY(:node_ids)
            RETURNING cluster_key
            """
        ),
        {
            "version": version,
            "domain_prefix": schema.cluster_key_domain_prefix(domain),
            "accepted": schema.DECISION_STATUS_ACCEPTED,
            "node_ids": node_ids,
        },
    ).fetchall()
    return [str(r[0] or "") for r in rows]


def list_pending_for_freeze(
    session: Any, *, domain_key: str, draft_node_ids: Iterable[str]
) -> list[dict]:
    """公開前チェック用「採用済みでまだ次版に反映されていない候補」（§5.4）。

    対象は当該ドメインの ``status='accepted'`` かつ ``applied_version=''`` のうち、
    ``draft_node_id`` が空（まだ下書きに取り込んでいない）か、現在の下書きの node 集合
    に無い（取り込みが失われた）もの。``draft_node_ids`` が空なら「下書きに何も入って
    いない」ので該当する採用済み候補はすべて未反映として返す。

    各要素は ``{cluster_key, proposed_label, layer, parent_region_id, draft_node_id,
    review_note}``。**件数ではなくラベルの列挙**で提示するための形（LS5）。
    ``proposed_label`` は生きている信号の最新表記、信号が無ければ cluster_key から
    復元した正規化ラベルへ縮退する。
    """
    domain = _clean(domain_key)
    if not domain:
        return []
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_DECISION_COLUMNS_SQL} FROM atlas_gap_decisions
             WHERE starts_with(cluster_key, :domain_prefix)
               AND status = :accepted
               AND applied_version = ''
             ORDER BY cluster_key
            """
        ),
        {
            "domain_prefix": schema.cluster_key_domain_prefix(domain),
            "accepted": schema.DECISION_STATUS_ACCEPTED,
        },
    ).fetchall()
    if not rows:
        return []

    node_set = {_clean(n) for n in (draft_node_ids or []) if _clean(n)}
    pending = [
        _decision_row_to_dict(r)
        for r in rows
    ]
    pending = [
        d
        for d in pending
        if not d["draft_node_id"] or d["draft_node_id"] not in node_set
    ]
    if not pending:
        return []

    labels: dict[str, dict] = {}
    for signal in _fetch_active_signals(session, domain_key=domain):
        key = schema.build_cluster_key(
            signal["domain_key"], signal["parent_region_id"], signal["proposed_label"]
        )
        labels.setdefault(key, signal)

    out: list[dict] = []
    for decision in pending:
        key = decision["cluster_key"]
        signal = labels.get(key)
        _, parent_region_id, normalized_label = schema.parse_cluster_key(key)
        out.append(
            {
                "cluster_key": key,
                "proposed_label": (
                    signal["proposed_label"] if signal else normalized_label
                ),
                "layer": signal["layer"] if signal else "",
                "parent_region_id": (
                    signal["parent_region_id"] if signal else parent_region_id
                ),
                "draft_node_id": decision["draft_node_id"],
                "review_note": decision["review_note"],
            }
        )
    return out


__all__ = [
    "derive_candidates",
    "get_decision",
    "list_active_signals",
    "list_pending_for_freeze",
    "mark_incorporated",
    "record_detect_audit",
    "record_signals",
    "restore_decision",
    "stamp_applied_versions",
    "upsert_decision",
]
