"""理論モジュールの保存行（theory_module_layer_design.md §13.2 / §13.5・migration 086）。

builder の保存用出力（``core/theory_modules/builder.py::build_theory_module_records``）を、
``core/knowledge_objects/sync.py::sync_live_rows`` が受け取る incoming（``{"stable_key",
"agent_id", "values"}``）へ変換する純関数だけを置く。書き込み（SQL）は
``core/document_pipeline/persistence.py::persist_theory_modules`` が受け持つ
（``learning_units.py`` ⇄ ``persist_learning_units`` と同じ分担）。

- stable_key は :func:`.stable_key.theory_module_stable_key`（生む式の equation stable_key 集合 +
  段 + 規則の版）。同一 run 内の衝突は :func:`.stable_key.assign_stable_keys`
  （``agent_id_of`` = ``module_key``）で ``#2`` … を付ける。
- 外枠を先に確定し、内側の ``parent_stable_key`` には親（外枠）の**確定済み**キーを入れる。
- 人間の確定列を持たない（承認オブジェクトを増やさない = PL5 の継承）。したがって
  ``sync_live_rows`` の ``preserved_columns`` は空。
- 構造の指紋（``structure_fingerprint``）は DB 列にだけ置く内部表現（TM12）。

DB・FastAPI・LLM を import しない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import stable_key as ko_keys

#: ``sync_live_rows`` の agent 側 ID 列（読み時 DTO の ``module_key``）。
AGENT_ID_COLUMN = "agent_module_key"

#: 一致時に上書きする内容列（``id`` / ``document_id`` / ``stable_key`` / ``agent_module_key`` /
#: ``produced_by_run_id`` / supersede 系・時刻は ``sync_live_rows`` が扱う）。人間の確定列は無い。
THEORY_MODULE_CONTENT_COLUMNS: tuple[str, ...] = (
    "rule_version",
    "level",
    "parent_stable_key",
    "label",
    "visual_label",
    "theory_object",
    "process_verbs",
    "dominant_stage",
    "stage_keys",
    "source_backing_status",
    "isolated_reason",
    "structure_fingerprint",
    "identity_eligible",
    "member_steps",
    "produced_equation_ids",
    "produced_equation_keys",
    "input_equation_ids",
    "output_equation_ids",
    "foundation_equation_ids",
    "sink_equation_ids",
    "required_claim_ids",
    "assumptions",
    "agent_payload",
)

LEVEL_OUTER = "outer"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if isinstance(item, (str, int)) and str(item).strip()]


def _member_rows(value: Any) -> list[dict]:
    rows: list[dict] = []
    for item in value if isinstance(value, (list, tuple)) else []:
        if not isinstance(item, Mapping):
            continue
        rows.append({
            "step_refs": _str_list(item.get("step_refs")),
            "node_ids": _str_list(item.get("node_ids")),
            "operation": _text(item.get("operation")),
            "edge_type": _text(item.get("edge_type")),
            "generic": bool(item.get("generic")),
            "stage_key": _text(item.get("stage_key")),
            "input_equation_ids": _str_list(item.get("input_equation_ids")),
            "output_equation_ids": _str_list(item.get("output_equation_ids")),
        })
    return rows


def build_theory_module_items(
    document_id: str,
    records: Sequence[Mapping[str, Any]],
    *,
    rule_version: str,
) -> list[dict]:
    """builder の ``records`` を ``sync_live_rows`` の incoming に変換する（純関数）。

    Args:
        document_id: 対象 document の UUID（stable_key の材料）。
        records: ``build_theory_module_records(...)["records"]``（外枠 → その内側 の順）。
        rule_version: builder が返した ``rule_version``（stable_key の材料・列の値）。

    Returns:
        外枠 → 内側 の順の incoming。空の ``records`` なら空リスト（呼び出し側は同期して
        全 live 行を superseded にする = TM14 の後半）。
    """
    rows = [record for record in records if isinstance(record, Mapping)]
    if not rows:
        return []
    outer = [record for record in rows if _text(record.get("level")) == LEVEL_OUTER]
    inner = [record for record in rows if _text(record.get("level")) != LEVEL_OUTER]

    def _raw_key(record: Mapping[str, Any]) -> str:
        return ko_keys.theory_module_stable_key(
            document_id,
            rule_version,
            _text(record.get("level")),
            _str_list(record.get("produced_equation_keys")),
        )

    # 外枠を先に確定する（内側の parent_stable_key は親の確定済みキーを指す）。
    ordered = outer + inner
    finals = ko_keys.assign_stable_keys(
        ordered,
        key_of=_raw_key,
        agent_id_of=lambda record: _text(record.get("module_key")),
    )
    # module_key は builder が 1 回の結果の中で一意にする（#n 付き）ので写像で引いてよい。
    key_by_module = {
        _text(record.get("module_key")): final
        for record, final in zip(ordered, finals)
        if _text(record.get("module_key"))
    }
    final_by_id = {id(record): final for record, final in zip(ordered, finals)}

    items: list[dict] = []
    for record in outer + inner:
        parent_module_key = _text(record.get("parent_module_key"))
        parent_stable_key = key_by_module.get(parent_module_key) if parent_module_key else None
        values = {
            "rule_version": _text(rule_version),
            "level": _text(record.get("level")),
            "parent_stable_key": parent_stable_key,
            "label": _text(record.get("label")),
            "visual_label": _text(record.get("visual_label")),
            "theory_object": _text(record.get("theory_object")),
            "process_verbs": _str_list(record.get("process_verbs")),
            "dominant_stage": _text(record.get("dominant_stage")),
            "stage_keys": _str_list(record.get("stage_keys")),
            "source_backing_status": _text(record.get("source_backing_status")),
            "isolated_reason": _text(record.get("isolated_reason")) or None,
            "structure_fingerprint": _text(record.get("structure_fingerprint")),
            "identity_eligible": bool(record.get("identity_eligible")),
            "member_steps": _member_rows(record.get("members")),
            "produced_equation_ids": _str_list(record.get("produced_equation_ids")),
            "produced_equation_keys": _str_list(record.get("produced_equation_keys")),
            "input_equation_ids": _str_list(record.get("input_equation_ids")),
            "output_equation_ids": _str_list(record.get("output_equation_ids")),
            "foundation_equation_ids": _str_list(record.get("foundation_equation_ids")),
            "sink_equation_ids": _str_list(record.get("sink_equation_ids")),
            "required_claim_ids": _str_list(record.get("required_claim_ids")),
            "assumptions": _str_list(record.get("assumptions")),
            "agent_payload": {
                "components_for_comparison": _str_list(record.get("components_for_comparison")),
            },
        }
        items.append({
            "stable_key": final_by_id[id(record)],
            "agent_id": _text(record.get("module_key")),
            "values": values,
        })
    return items


def level_counts(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """内部報告用の件数（``stage_outputs`` / 監査。教員 UI には出さない）。"""
    outer = sum(1 for item in items if _text((item.get("values") or {}).get("level")) == LEVEL_OUTER)
    return {"outer": outer, "inner": len(items) - outer}
