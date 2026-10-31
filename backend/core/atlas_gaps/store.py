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
from core import document_refs
from core.atlas_gaps import schema
from core.schema import AUDIT_ENTITY_CATEGORY_GAP, MAPPING_JUSTIFICATIONS

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
            SELECT {_SIGNAL_COLUMNS_SQL}, COALESCE(d.title, ''),
                   COALESCE(d.source_url, ''), COALESCE(d.filename, '')
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
        out = []
        for r in rows:
            entry = _signal_row_to_dict(r, title=(r[15] or ""))
            # 論文の同一性の材料（IK-0846）。DTO には載せない（数える・畳むための内部値）。
            entry["_source_url"] = (r[16] if len(r) > 16 else "") or ""
            entry["_filename"] = (r[17] if len(r) > 17 else "") or ""
            out.append(entry)
        return out
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


def resolved_label_set(frozen_skeleton: Any) -> set[str]:
    """現行凍結版で解消済みとみなす正規化ラベルの集合（IK-0990・純粋）。

    教員側の候補キュー（:func:`derive_candidates`）が「現行凍結版に同名の項目があって外す」
    ときと同じ判定（:func:`_skeleton_index` の ``resolved``）を、学習者側の論文の海の縁
    （``core/corpus_view.py``）も使うための公開の入口。判定を 2 か所に書かない。
    教員の判断（decisions）は読まない。
    """
    return set(_skeleton_index(frozen_skeleton)["resolved"])


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


def _fold_by_paper(members: list[dict]) -> dict[str, dict]:
    """cluster の信号を「論文」単位に畳む（IK-0846。重複取り込みは 1 本・読める題名を代表に）。

    同一 document から複数の信号が同じ cluster に入ることは通常無いが
    （record_signals が畳む）、あっても新しい方（``created_at DESC`` の先頭）を代表にする。
    """
    representatives = document_refs.representative_document_ids(
        {
            "document_id": signal["document_id"],
            "title": signal.get("document_title") or "",
            "source_url": signal.get("_source_url") or "",
            "filename": signal.get("_filename") or "",
        }
        for signal in members
    )
    by_document: dict[str, dict] = {}
    for signal in members:
        document_id = signal["document_id"]
        paper = representatives.get(document_id, document_id)
        current = by_document.get(paper)
        if current is None or (document_id == paper and current["document_id"] != paper):
            by_document[paper] = signal
    return by_document


def _paper_documents(by_document: Mapping[str, dict], version: str) -> list[dict]:
    """畳んだ信号 → 候補 DTO の ``documents``（題名・理由・逐語。生 confidence なし = LS5）。

    通常の候補（:func:`derive_candidates`）と導出から外れた判断（:func:`list_orphaned_decisions`
    — IK-0937）が**同じ形**で根拠を運ぶための 1 箇所。
    """
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
                "version_mismatch": bool(version and signal["skeleton_version"] != version),
                # LS5: 生値ではなく段階ラベル
                "confidence_label": schema.confidence_label(signal["confidence"]),
                "created_at": signal["created_at"],
            }
        )
    return documents


def gate_documents(
    documents: Iterable[Mapping[str, Any]], visible_document_ids: Iterable[str] | None
) -> tuple[list[dict], bool]:
    """閲覧できない論文を根拠の列挙から外す（IK-0937・fail-closed）。

    ``visible_document_ids`` が ``None`` のときは絞らない（呼び出し側が可視性を
    判定しない経路 = テスト・内部用）。戻り値は ``(残した論文, 外した論文があったか)`` —
    件数ではなく真偽だけを返す（LS5）。
    """
    docs = [dict(d) for d in documents or ()]
    if visible_document_ids is None:
        return docs, False
    visible = {_clean(v) for v in visible_document_ids if _clean(v)}
    kept = [d for d in docs if _clean(d.get("document_id")) in visible]
    return kept, len(kept) < len(docs)


def derive_candidates(
    session: Any,
    *,
    domain_key: str,
    frozen_skeleton: Any = None,
    current_version: str = "",
    include_dismissed: bool = False,
    report: dict | None = None,
) -> list[dict]:
    """レビューキュー（cluster 単位の候補）を**毎回導出**する（§4.3 / §5.2）。

    ``report`` を渡すと、候補にならなかった理由の**真偽**（件数ではない）を書き込む
    （IK-0851 第 2 周: 候補ゼロの理由を言い分ける / IK-0884: 導出から外れた判断行の
    理由を言う）::

        {"signals_present": bool,        # active な信号があったか
         "below_threshold": bool,        # 反復の条件に届かない cluster があったか
         "folded_duplicates": bool,      # 重複取り込みを 1 本に畳んだことで届かなかったか
         "resolved_present": bool,       # 現行版に同名の項目があって外したか
         "suppressed_present": bool,     # 見送り済み・統合済みで外したか
         "cluster_states": {cluster_key: "below_threshold" | "resolved"}}

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
    if report is not None:
        report.update(
            {
                "signals_present": bool(signals),
                "below_threshold": False,
                "folded_duplicates": False,
                "resolved_present": False,
                "suppressed_present": False,
                "cluster_states": {},
            }
        )
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
        # 反復の閾値は「論文」の数で数える（IK-0846）。同じ論文の PDF 版と TeX 版の
        # ような重複取り込みは 1 本に畳み、読める題名を持つ取り込みを代表にする。
        # 同一 document から複数の信号が同じ cluster に入ることは通常無いが
        # （record_signals が畳む）、あっても新しい方を代表にする。
        by_document = _fold_by_paper(members)
        if len(by_document) < schema.MIN_DOCUMENTS_FOR_CANDIDATE:
            if report is not None:
                report["below_threshold"] = True
                report["cluster_states"][cluster_key] = "below_threshold"
                if (
                    len({s["document_id"] for s in members})
                    >= schema.MIN_DOCUMENTS_FOR_CANDIDATE
                ):
                    report["folded_duplicates"] = True
            continue

        representative = members[0]  # created_at DESC 順（最新の表記を代表にする）
        if representative["normalized_label"] in resolved:
            # 現行凍結版に同名のノードがある = 解消済み（自然消滅）
            if report is not None:
                report["resolved_present"] = True
                report["cluster_states"][cluster_key] = "resolved"
            continue

        decision = decisions.get(cluster_key)
        if (
            decision is not None
            and decision["status"] in schema.SUPPRESSED_DECISION_STATUSES
            and not include_dismissed
        ):
            if report is not None:
                report["suppressed_present"] = True
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

        documents = _paper_documents(by_document, version)

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
    mapping_justification: str | None = None,
) -> dict:
    """cluster 単位の教員判断を記録する（``ON CONFLICT (cluster_key)`` で最新に上書き）。

    検証（いずれも ``ValueError``。route が 422 に変換する契約）:

    - ``cluster_key`` が空
    - ``decided_by`` が空（帰属必須・匿名の判断を作らない）
    - ``status`` が :data:`schema.DECISION_STATUSES` の語彙外
    - ``status='dismissed'`` で ``review_note`` が空（見送りは理由必須・§5.4）
    - ``status='merged'`` で ``merged_into`` が空（統合先の無い統合を作らない）
    - ``mapping_justification`` が ``core.schema.MAPPING_JUSTIFICATIONS`` の語彙外

    ``mapping_justification``（概念レジストリ KR4・migration 082）は「なぜこの候補を
    立てられたか」の記録。``None``（既定）のときは**書かない** — 既存行の値も消さない
    （記録が無いことを推測で埋めない）。

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
    justification = _clean(mapping_justification)
    if justification and justification not in MAPPING_JUSTIFICATIONS:
        raise ValueError(f"invalid mapping_justification: {mapping_justification!r}")

    row = session.execute(
        sa_text(
            f"""
            INSERT INTO atlas_gap_decisions (
                cluster_key, status, review_note, merged_into, decided_by, decided_at,
                mapping_justification
            ) VALUES (
                :cluster_key, :status, :review_note, :merged_into,
                CAST(:decided_by AS uuid), now(),
                NULLIF(:mapping_justification, '')
            )
            ON CONFLICT (cluster_key) DO UPDATE
               SET status = :status,
                   mapping_justification = CASE WHEN :mapping_justification <> ''
                        THEN :mapping_justification
                        ELSE atlas_gap_decisions.mapping_justification END,
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
            "mapping_justification": justification,
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


def _fetch_unapplied_accepted(session: Any, domain: str) -> list[dict]:
    """当該ドメインの ``accepted`` かつ ``applied_version=''`` の判断行（凍結のゲートの母集合）。"""
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
    return [_decision_row_to_dict(r) for r in rows or ()]


#: 導出から外れた判断行の理由（``orphaned_decisions[].reason``）。文言は
#: ``core/atlas_review_display.ORPHANED_REASON_FACTS`` が正本（ここは語彙だけ）。
ORPHAN_REASON_BELOW_THRESHOLD = "below_threshold"
ORPHAN_REASON_RESOLVED = "resolved"
ORPHAN_REASON_NO_SIGNALS = "no_signals"


def list_orphaned_decisions(
    session: Any,
    *,
    domain_key: str,
    derived_cluster_keys: Iterable[str],
    cluster_states: Mapping[str, str] | None = None,
    draft_node_ids: Iterable[str] | None = None,
    current_version: str = "",
    visible_document_ids: Iterable[str] | None = None,
) -> list[dict]:
    """導出の条件から外れたのに凍結のゲートが読む「採用済み・未反映」の判断行（IK-0884）。

    凍結のゲート（:func:`list_pending_for_freeze`）は判断行だけを読み、候補一覧
    （:func:`derive_candidates`）は導出された候補に判断を重ねる。導出の規則・材料が
    変わって cluster が候補の条件を外れると、判断行はゲートに残るのに一覧から消える。
    ここではその行を**別枠で**返し、画面に「戻す・取り込む」の入口を置けるようにする
    （ゲートは緩めない — 教員の採用を黙って捨てない）。

    各要素は ``{cluster_key, proposed_label, layer, layer_label, parent_region_id,
    reason, in_draft, documents, decision}``（+ 閲覧できない論文を外したときだけ
    ``documents_hidden: True``）。``documents`` は通常の候補と同じ形の根拠（題名・理由・
    逐語 — IK-0937）で、``visible_document_ids`` を渡すと閲覧できない論文を外す
    （fail-closed。件数ではなく真偽だけを返す）。``reason`` は :data:`ORPHAN_REASON_BELOW_THRESHOLD` /
    :data:`ORPHAN_REASON_RESOLVED` / :data:`ORPHAN_REASON_NO_SIGNALS`。``in_draft`` は
    取り込んだ node が現在の下書きにある（＝凍結のゲートでは止まらない）こと。
    件数フィールドは作らない（LS5）。
    """
    domain = _clean(domain_key)
    if not domain:
        return []
    derived = {_clean(k) for k in (derived_cluster_keys or ()) if _clean(k)}
    decisions = [
        d for d in _fetch_unapplied_accepted(session, domain) if d["cluster_key"] not in derived
    ]
    if not decisions:
        return []
    states = dict(cluster_states or {})
    node_set = {_clean(n) for n in (draft_node_ids or ()) if _clean(n)}
    latest: dict[str, dict] = {}
    members_by_key: dict[str, list[dict]] = {}
    for signal in _fetch_active_signals(session, domain_key=domain, with_titles=True):
        key = schema.build_cluster_key(
            signal["domain_key"], signal["parent_region_id"], signal["proposed_label"]
        )
        latest.setdefault(key, signal)
        members_by_key.setdefault(key, []).append(signal)
    out: list[dict] = []
    for decision in decisions:
        key = decision["cluster_key"]
        signal = latest.get(key)
        _, parent_region_id, normalized_label = schema.parse_cluster_key(key)
        layer = (signal or {}).get("layer") or (
            schema.GAP_LAYER_CONCEPT if parent_region_id else schema.GAP_LAYER_REGION
        )
        state = states.get(key)
        if state == "resolved":
            reason = ORPHAN_REASON_RESOLVED
        elif signal is None:
            reason = ORPHAN_REASON_NO_SIGNALS
        else:
            reason = ORPHAN_REASON_BELOW_THRESHOLD
        # 根拠の論文と逐語（IK-0937）。信号が残っている判断は、通常の候補と同じ形で
        # 運ぶ（外れた判断を原文に照らして判断し直せるように）。信号が消えた判断
        # （no_signals）は運ぶ材料が無い — 理由の事実文がそれを言う。
        documents, hidden = gate_documents(
            _paper_documents(_fold_by_paper(members_by_key.get(key, [])), _clean(current_version)),
            visible_document_ids,
        )
        item = {
            "cluster_key": key,
            "proposed_label": (signal or {}).get("proposed_label") or normalized_label,
            "layer": layer,
            "layer_label": schema.layer_label(layer),
            "parent_region_id": (signal or {}).get("parent_region_id") or parent_region_id,
            "reason": reason,
            "in_draft": bool(
                decision["draft_node_id"] and decision["draft_node_id"] in node_set
            ),
            "documents": documents,
            "decision": decision,
        }
        if hidden:
            item["documents_hidden"] = True
        out.append(item)
    return out


def cluster_documents(session: Any, *, domain_key: str, cluster_key: str) -> list[dict]:
    """1 cluster の生きている信号から、候補と同じ形の根拠の論文を返す（IK-0937 / IK-0939）。

    候補の条件を外れた（導出に無い）判断でも、信号が残っていれば根拠を引ける。
    可視性ゲートは呼び出し側（:func:`gate_documents`）。
    """
    domain = _clean(domain_key)
    key = _clean(cluster_key)
    if not domain or not key:
        return []
    members = [
        signal
        for signal in _fetch_active_signals(session, domain_key=domain, with_titles=True)
        if schema.build_cluster_key(
            signal["domain_key"], signal["parent_region_id"], signal["proposed_label"]
        )
        == key
    ]
    return _paper_documents(_fold_by_paper(members), "")


def list_pending_for_freeze(
    session: Any, *, domain_key: str, draft_node_ids: Iterable[str]
) -> list[dict]:
    """公開前チェック用「採用済みでまだ次版に反映されていない候補」（§5.4）。

    対象は当該ドメインの ``status='accepted'`` かつ ``applied_version=''`` のうち、
    ``draft_node_id`` が空（まだ下書きに取り込んでいない）か、現在の下書きの node 集合
    に無い（取り込みが失われた）もの。``draft_node_ids`` が空なら「下書きに何も入って
    いない」ので該当する採用済み候補はすべて未反映として返す。

    各要素は ``{cluster_key, proposed_label, layer, parent_region_id, draft_node_id,
    review_note, decision, documents}``（``decision`` は帰属の材料・``documents`` は根拠の
    論文 — IK-0939。どちらも表示用の写しは route が作る）。**件数ではなくラベルの列挙**で提示するための形（LS5）。
    ``proposed_label`` は生きている信号の最新表記、信号が無ければ cluster_key から
    復元した正規化ラベルへ縮退する。
    """
    domain = _clean(domain_key)
    if not domain:
        return []
    pending = _fetch_unapplied_accepted(session, domain)
    if not pending:
        return []

    node_set = {_clean(n) for n in (draft_node_ids or []) if _clean(n)}
    pending = [
        d
        for d in pending
        if not d["draft_node_id"] or d["draft_node_id"] not in node_set
    ]
    if not pending:
        return []

    labels: dict[str, dict] = {}
    members_by_key: dict[str, list[dict]] = {}
    for signal in _fetch_active_signals(session, domain_key=domain, with_titles=True):
        key = schema.build_cluster_key(
            signal["domain_key"], signal["parent_region_id"], signal["proposed_label"]
        )
        labels.setdefault(key, signal)
        members_by_key.setdefault(key, []).append(signal)

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
                # 帰属（IK-0939）。表示名への写しは route が行う（UUID を応答に出さない）。
                "decision": decision,
                # 根拠の論文（IK-0939。可視性ゲートは route が gate_documents で掛ける）。
                "documents": _paper_documents(
                    _fold_by_paper(members_by_key.get(key, [])), ""
                ),
            }
        )
    return out


__all__ = [
    "cluster_documents",
    "derive_candidates",
    "gate_documents",
    "get_decision",
    "list_active_signals",
    "list_orphaned_decisions",
    "list_pending_for_freeze",
    "mark_incorporated",
    "record_detect_audit",
    "record_signals",
    "restore_decision",
    "stamp_applied_versions",
    "upsert_decision",
]
