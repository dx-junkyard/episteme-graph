"""ゼミ前ブリーフ（Seminar Brief, 提案1 v1）の読み時合成（非LLM・読み取り専用）。

正本: docs/features/seminar_brief_mirroring_design.md §1（不変条項 SB1〜SB4）。

輪講の前に教員が対象論文の「賭け金」を10分で把握するための read-only 合成ビュー。
新テーブル・新LLMはゼロ（SB1）で、既存投影の組み合わせだけで組み立てる:

  ① 脆い前提       — D層 open-assumptions（``compile_open_assumptions`` の document 絞り込み）
  ② 一点吊りの支持線 — SL層 ``support_paths`` の ``level == "single"`` の事実文
  ③ 晴れ間          — 「このコーパスの中では検証記録が見つかりません。」の閉世界事実文（SL1）
  ④ 学習者からの問い — この論文を教材にするコースの素朴な問いシグナル（D1-5 の k-匿名集約の
    再利用・名前とレンジだけ）。記録が無ければ「まだありません」の事実文（IK-0593。
    学習者が意図して手渡すチャネル本体は v2 の例外設計書を経る — SB3）

原則:
  - SB2 数値を見せない — 件数・人数の生値を返さない（レンジ・段階ラベル・事実文のみ）。
    投影はホワイトリストで組み立て、最後に再帰安全網（``_strip_numeric_keys``）を通す。
  - SB4 誰が何を挙げたかの集計を作らない — 学習者個人・学習者別件数のクエリ経路を
    このモジュールに書かない。claim つまづき補助は既存の k-匿名集約
    （``core/reconstruction/stumble.py::get_stumble_summary``）の再利用のみ。
  - SL1 閉世界語彙 — 検証記録の不在について言えるのは「このコーパスの中では」だけ
    （分野レベルの不在言明を作らない。本モジュールは
    ``test_stakes_ledger_guardrails.py`` の denylist 検査対象に登録済み）。
  - FastAPI / LLM を import しない（テスタビリティ確保）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as sa_text

from core import display_projection as _dp
from core.doubt.naive_signal import aggregate_naive_signals_for_courses
from core.doubt.open_assumptions import compile_open_assumptions, target_label
from core.doubt.schema import LOAD_LEVEL_LABELS
from core.doubt.support_paths import build_support_context, compute_support_lines_from_context
from core.reconstruction.stumble import get_stumble_summary

logger = logging.getLogger(__name__)

# 晴れ間（区画③）の閉世界固定文（SL1）。「このコーパスの中では」を必ず含む肯定形 —
# 台帳はコーパスの射影であって分野の射影ではない。晴れ間は発見の候補地であって発見ではない。
FACT_LINE_NO_VERIFICATION_RECORD = "このコーパスの中では検証記録が見つかりません。"

# 第4区画（学習者からの問い）の事実文（SB3 / IK-0593）。警告色・催促文にしない（空欄は発見の流儀）。
# 記録が無いとき — 「実装後に使われます」と言わない（集約の仕組みは既にある。無いのは記録）。
LEARNER_HANDOFF_NO_RECORD_NOTE = "このコースの学習者の詰まりの記録はまだありません。"
# 記録があるとき、区画の出所を言う一行（件数・閾値の数字は書かない — SB2）。
LEARNER_HANDOFF_SOURCE_NOTE = (
    "学習者が自分で確かめた問いのうち、同じ箇所に複数の学習者が集まったものだけを示します"
    "（少人数の箇所は出しません）。"
)
# 1件の事実文（名前は statement 側。人数はレンジだけ — privacy.py の正本）。
LEARNER_HANDOFF_FACT_TEMPLATE = "複数の学習者が、ここで問いを立てています（{count_range} 人）。"

# 同じ文面の項目を 1 行にまとめたときの事実文（IK-0593。件数は書かない — SB2）。
MERGED_SAME_TEXT_NOTE = "同じ文面の項目をまとめて表示しています（同じ主張が複数の手順で使われています）。"
# 対象は別だが、読める手がかり（式の名前）でも見分けられない同じ文面の項目をまとめたとき。
MERGED_INDISTINCT_NOTE = "見分ける手がかりのない同じ文面の項目をまとめて表示しています。"

# 各区画の上限（順位づけの演出はしない。並び順は投影元の決定論順をそのまま使う）
_MAX_FRAGILE_ASSUMPTIONS = 8
_MAX_SINGLE_SUPPORT_LINES = 5
_MAX_CLEAR_SKIES = 8
_MAX_LEARNER_HANDOFF = 8
#: 項目を区別する式の名前の上限（「式 (3)・式 (4)」まで）。
_MAX_DISTINGUISHING_EQUATIONS = 2

# SB2 の再帰安全網: 万一投影に混入しても落とす生数値キー
# （core/discuss/opening.py::_strip_numeric_keys と同型。ホワイトリスト投影が主で、
#  これは最後の安全網）。
_FORBIDDEN_NUMERIC_KEYS = _dp.SEMINAR_BRIEF_FORBIDDEN_NUMERIC_KEYS  # 正本は display_projection（DP2）


def _strip_numeric_keys(value: Any) -> Any:
    """レスポンスを再帰走査して生数値キーを除去する（SB2 の最後の安全網）。"""
    return _dp.strip_keys(value, _FORBIDDEN_NUMERIC_KEYS)


def _resolve_document_id(session, document_ref: str) -> str:
    """document_ref（documents.id UUID か source_path=material_id）を正準 document_id に解決する。

    theory_component_graphs / epistemic_ledger / theory_claims の document_id 列は
    documents.id（テキスト表現）を持つため、ここで正準化してから各投影に渡す。
    """
    document_ref = str(document_ref or "").strip()
    if not document_ref:
        return ""
    try:
        row = session.execute(
            sa_text("""
                SELECT id::text
                FROM documents
                WHERE id::text = :ref OR source_path = :ref
                LIMIT 1
            """),
            {"ref": document_ref},
        ).fetchone()
    except Exception:
        logger.warning("seminar brief document resolution failed for %s", document_ref, exc_info=True)
        return ""
    return str(row[0]) if row else ""


def _derive_course_id(session, document_id: str) -> str:
    """document の解析グラフから course_id を導出する（``ledger_builder`` と同じ経路）。

    複数コースに同一 document が紐づく場合に返す course_id が実行ごとに揺れないよう、
    ``ORDER BY created_at DESC, course_id`` で決定論化する（最新の解析グラフを優先し、
    同時刻は course_id の辞書順で安定させる）。
    """
    try:
        rows = session.execute(
            sa_text("""
                SELECT course_id
                FROM theory_component_graphs
                WHERE document_id = CAST(NULLIF(:doc, '') AS uuid)
                ORDER BY created_at DESC, course_id
            """),
            {"doc": document_id},
        ).fetchall()
    except Exception:
        logger.warning("seminar brief course derivation failed for %s", document_id, exc_info=True)
        return ""
    for row in rows:
        course_id = str(row[0] or "").strip()
        if course_id:
            return course_id
    return ""


#: ノードの読める名前に添える根拠の主張本文の上限（語の途中で切らない）。
_NODE_CLAIM_EXCERPT_MAX = 90


class _ReadableLabels:
    """ブリーフの事実文から内部 ID を外すための読み時辞書（第 15 周 te-05 seq29 / 第 17 周 te-05）。

    式 ID は ``equation_semantics`` の記録から「式 (N)」/「番号なしの式（左辺 X）」に、
    グラフノード ID は**読める名前**（操作の日本語名 + 根拠の主張本文）に、それ以外は
    「（本文を特定できない要素）」に置き換える。取得失敗は fail-soft（置換語だけで ID は出さない）。

    依存グラフのノード名は A層の ``visual_label``（``Infer intermediate claim\nsynth_claim_0013 → clai``
    — 2 行目は 30 字で切られる）を優先するため、そのまま隠すと「Infer intermediate claim
    （本文を特定できない要素） → clai」になり中身が読めなかった。ノードの根拠主張
    （``claim_object_builder`` の本文）と操作の日本語名（``element_vocab.operation_label``）で組み直す。
    """

    def __init__(self, session, document_id: str, course_id: str = "") -> None:
        self.equation_records: dict = {}
        self.node_labels: dict[str, str] = {}
        self.claim_texts: dict[str, str] = {}
        # 項目の同一性・区別に使う読み時の補助（IK-0593。表示には ID を出さない）
        self.node_claim_ids: dict[str, list[str]] = {}
        self.node_operations: dict[str, str] = {}
        self.node_equation_ids: dict[str, list[str]] = {}
        graph = None
        try:
            from core.document_pipeline.persistence import document_run_artifacts

            artifacts = document_run_artifacts(document_id) or {}
            payload = artifacts.get("equation_semantics")
            payload = payload if isinstance(payload, dict) else {}
            for record in payload.get("records") or payload.get("equations") or []:
                if isinstance(record, dict) and record.get("equation_id"):
                    self.equation_records.setdefault(str(record["equation_id"]), record)
            claims_payload = artifacts.get("claim_object_builder")
            claims_payload = claims_payload if isinstance(claims_payload, dict) else {}
            for claim in claims_payload.get("claims") or claims_payload.get("claim_objects") or []:
                if not isinstance(claim, dict):
                    continue
                claim_id = str(claim.get("claim_id") or "").strip()
                text = str(claim.get("normalized_text") or claim.get("text") or "").strip()
                if claim_id and text:
                    self.claim_texts.setdefault(claim_id, text)
        except Exception:  # noqa: BLE001
            logger.debug("seminar brief artifacts unavailable for %s", document_id, exc_info=True)
        try:
            from core.doubt.dependency import build_dependency_graph

            graph = build_dependency_graph(session, course_id=course_id, document_id=document_id)
        except Exception:  # noqa: BLE001
            logger.debug("seminar brief node labels unavailable for %s", document_id, exc_info=True)
        if graph is not None:
            self.node_claim_ids = {k: list(v or []) for k, v in dict(getattr(graph, "node_claim_ids", {}) or {}).items()}
            self.node_operations = {k: str(v or "") for k, v in dict(getattr(graph, "node_operations", {}) or {}).items()}
            for eq_id, nids in dict(getattr(graph, "equation_refs", {}) or {}).items():
                for nid in nids or ():
                    eqs = self.node_equation_ids.setdefault(str(nid), [])
                    if eq_id not in eqs:
                        eqs.append(str(eq_id))
            for nid, raw in dict(graph.node_labels).items():
                self.node_labels[nid] = self._readable_node_label(
                    raw,
                    operation=str(getattr(graph, "node_operations", {}).get(nid) or ""),
                    claim_ids=list(getattr(graph, "node_claim_ids", {}).get(nid) or []),
                )

    def _claim_text(self, claim_id: str) -> str:
        from core.knowledge_objects.references import normalize_claim_ref

        text = self.claim_texts.get(claim_id)
        if not text:
            try:
                text = self.claim_texts.get(normalize_claim_ref(claim_id))
            except Exception:  # noqa: BLE001
                text = None
        return str(text or "")

    def _readable_node_label(self, raw: Any, *, operation: str, claim_ids: list[str]) -> str:
        from core.element_vocab import operation_label
        from core.text_excerpt import excerpt
        from core.theory_modules.schema import UNIDENTIFIED_ELEMENT_TEXT, mask_internal_ids_readable

        head = str(raw or "").split("\n", 1)[0].strip()
        op_ja = operation_label(operation)
        claim = ""
        for claim_id in claim_ids:
            text = self._claim_text(claim_id)
            if text:
                claim = excerpt(mask_internal_ids_readable(text, self.equation_records), _NODE_CLAIM_EXCERPT_MAX)
                if claim:
                    break
        masked_head = mask_internal_ids_readable(head, self.equation_records)
        generic_head = (
            not masked_head
            or UNIDENTIFIED_ELEMENT_TEXT in masked_head
            or _dp.contains_internal_id(head)
            or bool(op_ja and operation and _is_operation_phrase(masked_head, operation))
        )
        if claim and generic_head:
            return f"{op_ja}: {claim}" if op_ja else claim
        if generic_head:
            stripped = masked_head.replace(UNIDENTIFIED_ELEMENT_TEXT, "").strip()
            if op_ja and (not stripped or _is_operation_phrase(stripped, operation)):
                return op_ja
            return masked_head or op_ja or UNIDENTIFIED_ELEMENT_TEXT
        return masked_head

    def identity(self, item: dict) -> tuple:
        """項目が指す対象の同一性（IK-0593）。同じ主張を根拠にもつ同じ操作のノードは同じ対象とみなす。"""
        from core.knowledge_objects.references import normalize_claim_ref

        target_type = str(item.get("target_type") or "")
        target_id = str(item.get("target_id") or "")
        if target_type == "claim":
            return ("claim", normalize_claim_ref(target_id))
        if target_id in self.node_labels:
            claims = [normalize_claim_ref(c) for c in self.node_claim_ids.get(target_id) or [] if str(c or "").strip()]
            if claims:
                return ("node", claims[0], self.node_operations.get(target_id, ""))
        return (target_type, target_id)

    def distinguisher(self, item: dict) -> str:
        """同じ文面の別の対象を見分ける読める手がかり（式の名前。内部 ID・件数は使わない）。"""
        from core.theory_modules.schema import equation_display_name

        target_id = str(item.get("target_id") or "")
        names: list[str] = []
        for eq_id in sorted(self.node_equation_ids.get(target_id) or []):
            record = self.equation_records.get(eq_id)
            if not record:
                continue
            name = equation_display_name(record)
            if name and name not in names:
                names.append(name)
            if len(names) >= _MAX_DISTINGUISHING_EQUATIONS:
                break
        return "・".join(names)

    def text(self, value: Any) -> str:
        from core.theory_modules.schema import (
            UNIDENTIFIED_ELEMENT_TEXT,
            contains_internal_id,
            mask_internal_ids_readable,
        )

        raw = str(value or "").strip()
        if not raw:
            return ""
        node_label = self.node_labels.get(raw)
        if node_label and node_label != raw:
            raw = node_label
        if raw in self.equation_records or not contains_internal_id(raw):
            return mask_internal_ids_readable(raw, self.equation_records)
        masked = mask_internal_ids_readable(raw, self.equation_records)
        return masked or UNIDENTIFIED_ELEMENT_TEXT


def _is_operation_phrase(text: str, operation: str) -> bool:
    """``text`` が操作語彙の英語の言い回しそのもの（``Infer intermediate claim``）か。"""
    phrase = " ".join(str(operation or "").replace("_", " ").split()).lower()
    body = " ".join(str(text or "").split()).lower()
    return bool(phrase) and (body == phrase or body.startswith(phrase + " ") or body.startswith(phrase + "("))


def _readable_items(items: list[dict], labels: "_ReadableLabels", keys: tuple[str, ...]) -> None:
    for item in items:
        for key in keys:
            if key in item:
                item[key] = labels.text(item.get(key))


def _disambiguate_same_text(items: list[dict], labels: "_ReadableLabels", *, extra_key: str = "") -> list[dict]:
    """同じ文面の項目を、同じ対象なら 1 行にまとめ、別の対象なら式の名前で見分ける（IK-0593）。

    文面（``statement`` + ``extra_key`` の値）が同じ項目の群ごとに:
      1. 見分けの手がかり（ノードが使う式の名前）が群の中で違えば、手がかりのある項目の文面に
         「（式 (3)）」を添える。
      2. 手がかりでも分けられず残った項目は先頭の 1 件にまとめ ``merged_note`` を添える
         （同じ対象 = 同じ主張・同じ操作なら MERGED_SAME_TEXT_NOTE、別の対象なら
         MERGED_INDISTINCT_NOTE — 「同じ主張」と言えないものにそう書かない）。
    並びは入力の決定論順を保つ。内部 ID・件数は文面に出さない（PL7 / SB2）。
    """
    groups: dict[tuple, list[int]] = {}
    for idx, item in enumerate(items):
        key = (str(item.get("statement") or ""), str(item.get(extra_key) or "") if extra_key else "")
        groups.setdefault(key, []).append(idx)

    drop: set[int] = set()
    for key, indices in groups.items():
        if len(indices) < 2 or not key[0]:
            continue
        dist = {i: labels.distinguisher(items[i]) for i in indices}
        if len(set(dist.values())) > 1:
            for i in indices:
                if dist[i]:
                    items[i]["statement"] = f"{items[i]['statement']}（{dist[i]}）"
        buckets: dict[str, list[int]] = {}
        for i in indices:
            buckets.setdefault(dist[i] if len(set(dist.values())) > 1 else "", []).append(i)
        for bucket in buckets.values():
            if len(bucket) < 2:
                continue
            keeper = bucket[0]
            same_target = len({labels.identity(items[i]) for i in bucket}) == 1
            items[keeper]["merged_note"] = MERGED_SAME_TEXT_NOTE if same_target else MERGED_INDISTINCT_NOTE
            drop.update(bucket[1:])
    return [item for idx, item in enumerate(items) if idx not in drop]


def _courses_using_document(session, document_id: str) -> list[str]:
    """この論文を教材（sources）にもつ学習コースの id（第4区画の集約範囲）。取得失敗は空。"""
    try:
        rows = session.execute(
            sa_text("""
                SELECT lc.id::text
                FROM learning_courses lc
                JOIN documents d ON d.id = CAST(NULLIF(:doc, '') AS uuid)
                WHERE lc.data->'sources' @> jsonb_build_array(jsonb_build_object('material_id', d.source_path))
                   OR lc.data->'sources' @> jsonb_build_array(jsonb_build_object('material_id', d.id::text))
                   OR lc.data->'sources' @> jsonb_build_array(jsonb_build_object('document_id', d.id::text))
                ORDER BY lc.id
            """),
            {"doc": document_id},
        ).fetchall()
    except Exception:
        logger.debug("seminar brief course lookup failed for %s", document_id, exc_info=True)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []
    return [str(r[0]) for r in rows if r and r[0]]


def _document_member_ids(session, document_id: str, table: str, ids: list[str]) -> set[str]:
    """``ids`` のうち、この論文の live 行に実在するもの（第4区画を論文の範囲に絞る）。"""
    if not ids:
        return set()
    if table == "claim":
        sql = """
            SELECT id::text FROM theory_claims_live
            WHERE document_id = CAST(NULLIF(:doc, '') AS uuid)
              AND id::text = ANY(:ids)
        """
    elif table == "component":
        sql = """
            SELECT id::text FROM theory_components_live
            WHERE document_id = CAST(NULLIF(:doc, '') AS uuid)
              AND id::text = ANY(:ids)
        """
    else:
        return set()
    try:
        rows = session.execute(sa_text(sql), {"doc": document_id, "ids": list(ids)}).fetchall()
    except Exception:
        logger.debug("seminar brief member lookup failed for %s", document_id, exc_info=True)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return set()
    return {str(r[0]) for r in rows if r and r[0]}


def _learner_handoff(session, document_id: str, labels: "_ReadableLabels") -> dict:
    """区画④: この論文を教材にするコースの素朴な問いシグナル（D1-5 の k-匿名集約の再利用）。

    学習者個人・学習者別件数の経路はこのモジュールに書かない（SB4）— 集約は
    ``core/doubt/naive_signal.py``（本人が引き受けた行のみ・ユニーク学習者・n<3 非表示・
    レンジのみ）の公開関数に任せ、ここでは論文の範囲に絞って名前を読める語にするだけ。
    """
    course_ids = _courses_using_document(session, document_id)
    signals: list[dict] = []
    if course_ids:
        try:
            signals = aggregate_naive_signals_for_courses(session, course_ids)
        except Exception:  # noqa: BLE001
            logger.debug("seminar brief naive signals failed for %s", document_id, exc_info=True)
            signals = []

    by_type: dict[str, list[str]] = {}
    for sig in signals:
        by_type.setdefault(str(sig.get("anchor_type") or ""), []).append(str(sig.get("anchor_id") or ""))
    members = {
        "claim": _document_member_ids(session, document_id, "claim", by_type.get("claim", [])),
        "component": _document_member_ids(session, document_id, "component", by_type.get("component", [])),
        "equation": {eid for eid in by_type.get("equation", []) if eid in labels.equation_records},
    }

    items: list[dict] = []
    for sig in signals:
        anchor_type = str(sig.get("anchor_type") or "")
        anchor_id = str(sig.get("anchor_id") or "")
        count_range = str(sig.get("count_range") or "")
        if not count_range or anchor_id not in members.get(anchor_type, set()):
            continue
        if anchor_type in ("claim", "component"):
            name = labels.text(target_label(session, anchor_type, anchor_id))
        else:
            name = labels.text(anchor_id)
        if not name or name == anchor_id or _dp.UNIDENTIFIED_ELEMENT_TEXT in name:
            continue  # 名前を読める語にできない箇所は出さない（内部 ID・置換語だけの行を作らない）
        items.append({
            "anchor_type": anchor_type,
            "statement": name,
            "fact_line": LEARNER_HANDOFF_FACT_TEMPLATE.format(count_range=count_range),
        })
        if len(items) >= _MAX_LEARNER_HANDOFF:
            break
    if not items:
        return {"reserved": False, "items": [], "note": LEARNER_HANDOFF_NO_RECORD_NOTE}
    return {"reserved": False, "items": items, "note": LEARNER_HANDOFF_SOURCE_NOTE}


def _project_fragile_item(item: dict) -> dict:
    """区画①の1件をホワイトリスト投影する（SB2）。

    ``compile_open_assumptions`` の item から段階ラベル・事実のみを転記し、
    ``dependent_count`` 等の生数値キーは持ち込まない。SL 4キー
    （has_falsification_condition / falsification_not_formulable /
    reachability_summary / support_line_level）は段階表示のままそのまま通す。
    """
    load_level = str(item.get("load_level") or "")
    return {
        "target_id": str(item.get("target_id") or ""),
        "target_type": str(item.get("target_type") or ""),
        "statement": str(item.get("statement") or ""),
        "verification_status": str(item.get("verification_status") or ""),
        "scope_coverage": str(item.get("scope_coverage") or ""),
        "load_level": load_level,
        "load_level_label": LOAD_LEVEL_LABELS.get(load_level, load_level),
        "challenge_count_label": str(item.get("challenge_count_label") or ""),
        "challenge_types": list(item.get("challenge_types") or []),
        "has_verification_proposal": bool(item.get("has_verification_proposal")),
        "has_naive_signal": bool(item.get("has_naive_signal")),
        # SL 4キー（賭け金の台帳の段階表示。数値は元から含まれない）
        "has_falsification_condition": bool(item.get("has_falsification_condition")),
        "falsification_not_formulable": bool(item.get("falsification_not_formulable")),
        "reachability_summary": str(item.get("reachability_summary") or ""),
        "support_line_level": str(item.get("support_line_level") or ""),
    }


def _attach_stumble_axes(document_id: str, fragile_items: list[dict]) -> None:
    """区画①の claim target に、claim つまづきサマリーの**段階ラベルのみ**を添える。

    既存の k-匿名集約（``get_stumble_summary``、k=3・レンジ/段階ラベル）の再利用のみで、
    学習者個人・学習者別件数のクエリはここに書かない（SB4）。``n_items`` 等の生数値は
    落とす（SB2）。取得失敗はブリーフ本体を壊さず補助情報なしで返す（fail-soft）。
    """
    claim_ids = [i["target_id"] for i in fragile_items if i.get("target_type") == "claim"]
    if not claim_ids:
        return
    try:
        summary = get_stumble_summary(document_id, claim_ids=claim_ids)
    except Exception:
        logger.debug("seminar brief stumble summary lookup failed for %s", document_id, exc_info=True)
        return
    axes_by_claim: dict[str, dict] = {}
    for row in summary.get("claims", []):
        if isinstance(row, dict) and row.get("claim_id"):
            # axes は段階ラベル・レンジのみ（error_rate の rate_level 等）。n_items は転記しない。
            axes_by_claim[str(row["claim_id"])] = {
                "axes": row.get("axes") or {},
                "has_data": bool(row.get("has_data")),
            }
    for item in fragile_items:
        stumble = axes_by_claim.get(item["target_id"])
        if item.get("target_type") == "claim" and stumble:
            item["stumble"] = stumble


def _single_support_lines(
    session, course_id: str, document_id: str, labels: "_ReadableLabels | None" = None
) -> list[dict]:
    """区画②: document の台帳対象のうち独立支持経路が1本（level=single）のものの事実文。

    共有文脈（``build_support_context``）は**1回だけ**構築し、対象ごとに
    ``compute_support_lines_from_context`` を回す（``compute_support_lines`` を
    対象数分呼んでグラフを N 回再構築する既知のアンチパターンを避ける）。
    """
    try:
        rows = session.execute(
            sa_text("""
                SELECT target_id, target_type
                FROM epistemic_ledger
                WHERE document_id = CAST(NULLIF(:doc, '') AS uuid)
            """),
            {"doc": document_id},
        ).fetchall()
    except Exception:
        logger.warning("seminar brief ledger target lookup failed for %s", document_id, exc_info=True)
        return []
    if not rows:
        return []

    ctx = build_support_context(session, course_id=course_id, document_id=document_id)
    if ctx is None:
        return []
    if labels is not None and labels.node_labels:
        # 崩れる対象（cut_members）の名前も読める名前で組む（事実文に生の英語操作名 +
        # 「（本文を特定できない要素）」が並ぶのを防ぐ — 第 17 周 te-05）。
        try:
            ctx.graph.node_labels.update(labels.node_labels)
        except Exception:  # noqa: BLE001
            logger.debug("seminar brief node label override failed", exc_info=True)

    out: list[dict] = []
    for target_id, target_type in sorted((str(r[0]), str(r[1])) for r in rows):
        try:
            lines = compute_support_lines_from_context(ctx, target_type, target_id)
        except Exception:  # noqa: BLE001
            logger.debug(
                "seminar brief support line computation failed for %s/%s",
                target_type, target_id, exc_info=True,
            )
            continue
        if not lines or str(lines.get("level") or "") != "single":
            continue
        out.append({
            "target_id": target_id,
            "target_type": target_type,
            "statement": target_label(session, target_type, target_id),
            # level=single の事実文をそのまま使う（数値は元から含まれない, SL4）
            "fact_line": str(lines.get("fact_line") or ""),
        })
        if len(out) >= _MAX_SINGLE_SUPPORT_LINES:
            break
    return out


def _clear_skies(assumption_items: list[dict], limit: int | None = _MAX_CLEAR_SKIES) -> list[dict]:
    """区画③: open_assumptions 投影のうち untested × スコープ空欄の対象に閉世界固定文を添える。"""
    out: list[dict] = []
    for item in assumption_items:
        if str(item.get("verification_status") or "") != "untested":
            continue
        if not item.get("scope_count_is_zero"):
            continue
        out.append({
            "target_id": str(item.get("target_id") or ""),
            "target_type": str(item.get("target_type") or ""),
            "statement": str(item.get("statement") or ""),
            "fact_line": FACT_LINE_NO_VERIFICATION_RECORD,
        })
        if limit is not None and len(out) >= limit:
            break
    return out


REASON_NO_LEDGER = (
    "この教材には、前提や主張の検証状態を記す台帳の行がまだ作られていないため、"
    "ブリーフを合成できません。台帳は解析の最後に作られます。解析が完了しているのに"
    "この表示が続く場合は、教材行の「パイプラインを実行」から再実行すると作られます。"
)


def _ledger_course_for_document(session, document_id: str) -> tuple[bool, str]:
    """台帳にこの document の行があるか・あればその course_id（空でもよい）。"""
    try:
        rows = session.execute(
            sa_text("""
                SELECT DISTINCT course_id
                FROM epistemic_ledger
                WHERE document_id = CAST(NULLIF(:doc, '') AS uuid)
                ORDER BY course_id DESC
            """),
            {"doc": document_id},
        ).fetchall()
    except Exception:
        logger.warning("seminar brief ledger course lookup failed for %s", document_id, exc_info=True)
        return False, ""
    if not rows:
        return False, ""
    return True, str(rows[0][0] or "")


def build_seminar_brief(session, document_ref: str) -> dict:
    """ゼミ前ブリーフを読み時合成する（書き込みなし・LLM 0回）。

    document_ref → document 解決と course_id 導出ができない場合は
    ``{"available": False, "reason": ...}`` の正直縮退（エラーにしない）。
    """
    document_id = _resolve_document_id(session, document_ref)
    if not document_id:
        return {
            "available": False,
            "reason": "指定された文献が見つかりませんでした。",
        }
    course_id = _derive_course_id(session, document_id)
    if not course_id:
        # パイプライン由来の解析グラフはコースに依らない（course_id が空）。台帳もその
        # document の行を course_id='' で持つので、コース対応が無いことを「解析未完了」と
        # 言わず、台帳の有無で判定する（第 14 周 brain-te03 / te-05 seq20 の食い違い）。
        has_ledger, ledger_course = _ledger_course_for_document(session, document_id)
        if not has_ledger:
            return {
                "available": False,
                "document_id": document_id,
                "reason": REASON_NO_LEDGER,
            }
        course_id = ledger_course

    # 区画①の投影元（教員向けブリーフだが疑義者名は載せない — include_challenger_names=False 固定）
    assumption_items = compile_open_assumptions(
        session, course_id, include_challenger_names=False, document_id=document_id,
    )
    labels = _ReadableLabels(session, document_id, course_id)
    # 読める文面にしてから同じ文面の項目を畳み・見分け（IK-0593）、そのあとで上限を掛ける
    # （重複が上限枠を食って別の前提が落ちるのを避ける）。
    fragile = [_project_fragile_item(i) for i in assumption_items]
    _readable_items(fragile, labels, ("statement",))
    fragile = _disambiguate_same_text(fragile, labels)[:_MAX_FRAGILE_ASSUMPTIONS]
    _attach_stumble_axes(document_id, fragile)

    single_lines = _single_support_lines(session, course_id, document_id, labels)
    skies = _clear_skies(assumption_items, limit=None)
    _readable_items(single_lines, labels, ("statement", "fact_line"))
    _readable_items(skies, labels, ("statement",))
    single_lines = _disambiguate_same_text(single_lines, labels, extra_key="fact_line")
    skies = _disambiguate_same_text(skies, labels)[:_MAX_CLEAR_SKIES]
    handoff = _learner_handoff(session, document_id, labels)

    brief = {
        "available": True,
        "document_id": document_id,
        "course_id": course_id,
        # ① 脆い前提（未検証 × 下流影響「高」— 段階ラベルのみ）
        "fragile_assumptions": fragile,
        # ② 一点吊りの支持線（level=single の事実文）
        "single_support_lines": single_lines,
        # ③ 晴れ間（閉世界の固定事実文）
        "clear_skies": skies,
        # ④ 学習者からの問い — 素朴な問いシグナルの k-匿名集約（名前とレンジのみ）か
        #    「まだありません」の事実文（SB3 / IK-0593）
        "learner_handoff": handoff,
    }
    return _strip_numeric_keys(brief)
