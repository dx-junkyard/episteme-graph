"""式スケール ELICIT（支配項の直感道場）の非LLM・決定論出題生成。

理解サイクル Phase 2（`docs/features/understanding_cycle_design.md` §6）が R層に追加する
``elicit_mode='regime'`` / ``'next_step'`` の出題は、LLM を使わず ``derivation_chain``
artifact（`src/episteme_graph/agents/derivation_chain/`）の**近似・削減系 operation の
地点のみ**から決定論的に組み立てる（恒等変形・定義・汎用変換には出題しない）。

- ``next_step``: 「この入力式からこの出力式へ進むために使った操作はどれか」の選択式。
- ``regime``: 「この操作のあとで取り除かれる記号はどれか」の選択式
  （``eliminated_symbols`` / ``retained_symbols`` を使う）。

足切り（生成条件）:
  ① step の ``operation`` が :data:`REGIME_OPERATIONS` に含まれる
  ② step または chain の ``source_evidence_ids`` が非空
  ③ claim UUID が ``theory_claims.source_scope.legacy_ids`` 経由で解決できる

claim 側の承認状態（source_backed + 承認済み review_status）はここでは見ない
（配信 SQL である `routes/reconstruction.py::get_next_item` が守る）。

本モジュールは純粋関数 + 1回の DB 読み取り（claim id 索引）のみで、FastAPI / LLM を
import しない（core/reconstruction 既存ガードレールを継承）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text as sa_text

from core.deliberation.labels import equation_label
from core.deliberation.refs import derivation_records, document_run_artifacts, equation_records
from core.element_vocab import operation_label
from core.postgres import get_session as _pg_session

# 近似・削減系の operation のみを出題対象にする（design 裁定）。恒等変形・定義・
# 汎用変換（substitute / transform / define / relate / apply_* 等）は出題しない —
# 「この論文が選んだ近似・簡約」の直感を問う設計意図と無関係なノイズになるため。
REGIME_OPERATIONS = frozenset({
    "approximate",
    "linearize",
    "normalize",
    "eliminate",
    "eliminate_parameter",
    "eliminate_variable",
    "compare",
    "branch_on_condition",
    "flag_limitation",
})

# next_step のディストラクタが同じ chain 内で尽きたときの決定論的フォールバック順
# （REGIME_OPERATIONS ∪ 主要統制語彙。恒等変形寄りの define/relate/transform/substitute/
# apply_* はノイズが強いため含めない）。
_FALLBACK_OPERATION_ORDER = (
    "approximate",
    "linearize",
    "normalize",
    "eliminate",
    "eliminate_parameter",
    "eliminate_variable",
    "compare",
    "branch_on_condition",
    "flag_limitation",
    "solve",
    "solve_linear_system",
    "derive",
    "derive_result",
    "constrain",
    "integrate",
)

_MAX_DISTRACTORS = 3


def _dict_list(value: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    items = value.get(key)
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def _clamp01(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _equation_label_index(document_id: str, artifacts: dict[str, Any]) -> dict[str, str]:
    """equation_id → 表示ラベル（`core.deliberation.labels.equation_label` 経由）の索引。

    latex・raw_text・内部 ID は使わない（labels.py の規約をそのまま継承）。
    """
    index: dict[str, str] = {}
    for record in equation_records(document_id, artifacts=artifacts):
        eq_id = str(record.get("equation_id") or "")
        if not eq_id:
            continue
        label = equation_label(record).text
        if label:
            index[eq_id] = label
    return index


def _equation_display(label_index: dict[str, str], equation_ids: list[Any], fallback: str) -> str:
    for eid in equation_ids or []:
        label = label_index.get(str(eid))
        if label:
            return label
    return fallback


def _claim_id_lookup(document_id: str) -> dict[str, str]:
    """theory_claims.source_scope.legacy_ids から「agent 側 claim id → DB UUID」の索引を組む。

    ``core.deliberation.context_lens._claim_id_lookup_from_rows`` と同じロジックの
    独立実装（W層と R層の直接結合を増やさないための複製。ロジック変更時は両方に留意）。
    document につき1回だけ SELECT する。
    """
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("SELECT id::text AS id, source_scope FROM theory_claims WHERE document_id = :doc"),
            {"doc": document_id},
        ).fetchall()
    finally:
        session.close()
    lookup: dict[str, str] = {}
    for r in rows:
        db_id = str(r[0])
        lookup[db_id] = db_id
        scope = r[1] if isinstance(r[1], dict) else {}
        for legacy_id in scope.get("legacy_ids") or []:
            key = str(legacy_id or "").strip()
            if key:
                lookup[key] = db_id
        span_id = scope.get("span_id")
        if span_id:
            lookup.setdefault(str(span_id), db_id)
    return lookup


def _first_resolved_claim_uuid(
    claim_lookup: dict[str, str],
    step: dict[str, Any],
    chain: dict[str, Any],
) -> str | None:
    """``positioning._chain_position_for_claim`` と同じ4経路（step→chain）で claim UUID を解決する。

    順序: step.input_claim_ids → step.output_claim_ids → step.required_claim_ids →
    step.assumption_ids → chain.input_claim_ids → chain.output_claim_ids →
    chain.assumption_ids（chain レベルに required_claim_ids は存在しない）。
    """
    for source in (
        step.get("input_claim_ids"),
        step.get("output_claim_ids"),
        step.get("required_claim_ids"),
        step.get("assumption_ids"),
        chain.get("input_claim_ids"),
        chain.get("output_claim_ids"),
        chain.get("assumption_ids"),
    ):
        for raw_id in source or []:
            resolved = claim_lookup.get(str(raw_id))
            if resolved:
                return resolved
    return None


def _source_evidence_ids(step: dict[str, Any], chain: dict[str, Any]) -> list[str]:
    ids = step.get("source_evidence_ids") or chain.get("source_evidence_ids") or []
    return [str(x) for x in ids if str(x or "").strip()]


def _distractor_operations(chain_steps: list[dict[str, Any]], exclude: str, limit: int) -> list[str]:
    """同じ chain の他 step の operation を出現順に、足りなければ固定順で補う。

    ``operation_label`` が空文字（未知キー）の語、および訳語が正解と重複する語は
    スキップする（ラベル重複は正解を推測可能にしてしまうため）。
    """
    seen_ops: set[str] = {exclude}
    seen_labels: set[str] = set()
    exclude_label = operation_label(exclude)
    if exclude_label:
        seen_labels.add(exclude_label)

    out: list[str] = []

    def _consider(op: str) -> bool:
        if not op or op in seen_ops:
            return False
        label = operation_label(op)
        if not label or label in seen_labels:
            return False
        seen_ops.add(op)
        seen_labels.add(label)
        out.append(op)
        return True

    for other in chain_steps:
        if len(out) >= limit:
            break
        _consider(str(other.get("operation") or "").strip())

    for op in _FALLBACK_OPERATION_ORDER:
        if len(out) >= limit:
            break
        _consider(op)

    return out[:limit]


def _next_step_probe(
    step: dict[str, Any],
    chain: dict[str, Any],
    document_id: str,
    claim_uuid: str,
    label_index: dict[str, str],
    chain_steps: list[dict[str, Any]],
) -> dict[str, Any] | None:
    operation = str(step.get("operation") or "").strip()
    op_label = operation_label(operation)
    if not op_label:
        return None

    distractor_ops = _distractor_operations(chain_steps, operation, _MAX_DISTRACTORS)
    correct_id = "op_" + operation
    options = [{"id": correct_id, "label": op_label}]
    for op in distractor_ops:
        options.append({"id": "op_" + op, "label": operation_label(op)})
    if len(options) < 2:
        # ディストラクタが1件も作れない場合、選択式が成立しないため出題しない。
        return None

    step_id = str(step.get("step_id") or "")
    derivation_id = str(chain.get("derivation_id") or "")
    input_label = _equation_display(label_index, step.get("input_equation_ids") or [], "前の式")
    output_label = _equation_display(label_index, step.get("output_equation_ids") or [], "次の式")
    prompt = (
        "この導出で、『" + input_label + "』から『" + output_label +
        "』へ進むためにこの論文が使った操作はどれだと思いますか？"
    )
    return {
        "claim_uuid": claim_uuid,
        "document_id": document_id,
        "elicit_mode": "next_step",
        "prompt": prompt,
        "response_space": options,
        "expected": {
            "option_id": correct_id,
            "operation": operation,
            "derivation_id": derivation_id,
            "step_id": step_id,
        },
        "author_confidence": _clamp01(step.get("confidence")),
        "claim_fields_used": ["derivation_chain"],
    }


def _regime_probe(
    step: dict[str, Any],
    chain: dict[str, Any],
    document_id: str,
    claim_uuid: str,
) -> dict[str, Any] | None:
    eliminated = [str(s).strip() for s in (step.get("eliminated_symbols") or []) if str(s or "").strip()]
    retained = [str(s).strip() for s in (step.get("retained_symbols") or []) if str(s or "").strip()]
    if not eliminated or not retained:
        return None

    target = eliminated[0]
    distractors: list[str] = []
    for s in retained:
        if s == target or s in distractors:
            continue
        distractors.append(s)
        if len(distractors) >= _MAX_DISTRACTORS:
            break
    if not distractors:
        # 正解と区別できるディストラクタが無ければ選択式が成立しないため出題しない。
        return None

    operation = str(step.get("operation") or "").strip()
    op_label = operation_label(operation) or operation
    step_id = str(step.get("step_id") or "")
    derivation_id = str(chain.get("derivation_id") or "")

    options = [{"id": "sym_0", "label": target}]
    for i, s in enumerate(distractors, start=1):
        options.append({"id": "sym_" + str(i), "label": s})

    prompt = "この導出の『" + op_label + "』の操作のあと、式から取り除かれる記号はどれだと思いますか？"
    return {
        "claim_uuid": claim_uuid,
        "document_id": document_id,
        "elicit_mode": "regime",
        "prompt": prompt,
        "response_space": options,
        "expected": {
            "option_id": "sym_0",
            "symbol": target,
            "derivation_id": derivation_id,
            "step_id": step_id,
        },
        "author_confidence": _clamp01(step.get("confidence")),
        "claim_fields_used": ["derivation_chain"],
    }


def collect_derivation_probes(document_id: str) -> list[dict[str, Any]]:
    """document の derivation_chain artifact から regime / next_step probe を集める（非LLM）。

    足切り: ①operation が :data:`REGIME_OPERATIONS` に含まれる step のみ ②step または
    chain の ``source_evidence_ids`` が非空 ③claim UUID が DB で解決できる。regime probe は
    さらに ``eliminated_symbols`` / ``retained_symbols`` が両方非空の step のみ対象。

    claim 側の承認（support_status / review_status）はここでは判定しない（配信 SQL の責務）。
    """
    doc_id = str(document_id or "").strip()
    if not doc_id:
        return []

    artifacts = document_run_artifacts(doc_id)
    chains = derivation_records(doc_id, artifacts=artifacts)
    if not chains:
        return []

    label_index = _equation_label_index(doc_id, artifacts)
    claim_lookup = _claim_id_lookup(doc_id)
    if not claim_lookup:
        return []

    probes: list[dict[str, Any]] = []
    for chain in chains:
        steps = _dict_list(chain, "steps")
        for step in steps:
            operation = str(step.get("operation") or "").strip()
            if operation not in REGIME_OPERATIONS:
                continue
            if not _source_evidence_ids(step, chain):
                continue
            claim_uuid = _first_resolved_claim_uuid(claim_lookup, step, chain)
            if not claim_uuid:
                continue

            next_step_probe = _next_step_probe(step, chain, doc_id, claim_uuid, label_index, steps)
            if next_step_probe:
                probes.append(next_step_probe)

            regime_probe = _regime_probe(step, chain, doc_id, claim_uuid)
            if regime_probe:
                probes.append(regime_probe)

    return probes
