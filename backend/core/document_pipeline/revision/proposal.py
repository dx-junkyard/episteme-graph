"""Revision-operation proposal generation from audit findings (#410 P1-6).

Turns ``requires_revision`` audit results into explicit revision operations,
either via an LLM (production) or supplied raw (debug). Every proposed operation
is validated **server-side**: schema, target existence, evidence existence, and
traceability (checkpoint / source locations) are enforced before the operation
is allowed into candidate assembly. Proposals remain editable by the user; the
generator never auto-accepts anything.
"""
from __future__ import annotations

import json
from typing import Callable

from .operations import ENTITY_REGISTRY, OPERATIONS, make_operation

_BUCKET = {
    "claim": "claims", "equation": "equations", "evidence": "evidence",
    "derivation": "derivations", "component": "components",
    "graph_node": "graph_nodes", "graph_edge": "graph_edges", "thesis": "thesis",
}
_MODIFYING = {"update_entity", "split_entity", "merge_entities", "remove_entity",
              "add_relation", "remove_relation", "relink_evidence"}


def _entity_exists(inventory: dict, target_type: str, target_id: str) -> bool:
    bucket = _BUCKET.get(target_type)
    if not bucket:
        return False
    return target_id in (inventory.get("entities", {}).get(bucket, {}) or {})


def _known_evidence_ids(inventory: dict) -> set[str]:
    return set((inventory.get("entities", {}).get("evidence", {}) or {}).keys())


def validate_proposed_operation(raw_op: dict, inventory: dict, audit_result: dict | None = None) -> tuple:
    """Validate a single proposed operation server-side.

    Returns ``(operation | None, reason)``. ``operation`` is a normalized op dict
    with traceability fields populated from the audit result; ``reason`` explains
    a rejection.
    """
    if not isinstance(raw_op, dict):
        return None, "not_an_object"
    operation = raw_op.get("operation")
    if operation not in OPERATIONS:
        return None, f"unknown_operation:{operation}"
    target_type = raw_op.get("target_type")
    if target_type not in ENTITY_REGISTRY:
        return None, f"unknown_target_type:{target_type}"
    target_id = str(raw_op.get("target_id") or "")

    # Modifying ops must target an entity that exists in the base inventory.
    if operation in _MODIFYING and operation != "add_entity":
        if not target_id or not _entity_exists(inventory, target_type, target_id):
            return None, f"unknown_target:{target_type}:{target_id}"

    # Evidence refs must resolve (source-derived, from the audit result).
    known_ev = _known_evidence_ids(inventory)
    audit_result = audit_result or {}
    evidence_refs = [str(e) for e in (raw_op.get("evidence_refs")
                                      or audit_result.get("evidence_refs") or []) if e]
    evidence_refs = [e for e in evidence_refs if e in known_ev]

    checkpoint_ids = list(raw_op.get("checkpoint_ids") or [])
    if audit_result.get("checkpoint_id") and audit_result["checkpoint_id"] not in checkpoint_ids:
        checkpoint_ids.append(audit_result["checkpoint_id"])

    op = make_operation(
        operation=operation,
        target_type=target_type,
        target_id=target_id,
        before_json=raw_op.get("before_json"),
        after_json=raw_op.get("after_json"),
        reason=raw_op.get("reason") or (audit_result.get("findings") or [{}])[0].get("detail", "")
            if audit_result.get("findings") else raw_op.get("reason", ""),
        checkpoint_ids=checkpoint_ids,
        evidence_refs=evidence_refs,
        source_locations=raw_op.get("source_locations") or audit_result.get("source_locations") or [],
        confidence=float(raw_op.get("confidence") or audit_result.get("confidence") or 0.0),
        operation_id=raw_op.get("operation_id") or "",
        source_ids=raw_op.get("source_ids") or raw_op.get("target_ids") or [],
        relation_target_type=raw_op.get("relation_target_type"),
        relation_target_id=raw_op.get("relation_target_id"),
    )
    return op, ""


def validate_proposed_operations(raw_ops: list, inventory: dict) -> dict:
    """Validate a batch of raw proposed operations (e.g. from the debug raw-JSON path)."""
    accepted, rejected = [], []
    for raw in raw_ops or []:
        op, reason = validate_proposed_operation(raw, inventory)
        if op is not None:
            accepted.append(op)
        else:
            rejected.append({"raw": raw, "reason": reason})
    return {"operations": accepted, "rejected": rejected}


def _proposal_messages(audit_result: dict, inventory: dict) -> list[dict]:
    bucket = _BUCKET.get(audit_result.get("target_type"))
    entity = (inventory.get("entities", {}).get(bucket, {}) or {}).get(
        audit_result.get("target_id"), {}) if bucket else {}
    instruction = (
        "あなたは知識グラフの編集者です。監査結果(audit)に基づき、対象entityを修正する"
        "revision operationを1つ提案してください。operationは次から選ぶ: "
        + ", ".join(OPERATIONS) +
        "。出力は次のJSONのみ: {\"operation\":..,\"target_type\":..,\"target_id\":..,"
        "\"after_json\":..,\"reason\":..,\"evidence_refs\":[..]}。"
        "原文根拠(evidence_refs)を必ず付け、推測だけで確定しないこと。"
    )
    payload = {"audit": {
        "verdict": audit_result.get("verdict"),
        "findings": audit_result.get("findings"),
        "target_type": audit_result.get("target_type"),
        "target_id": audit_result.get("target_id"),
        "evidence_refs": audit_result.get("evidence_refs"),
        "source_locations": audit_result.get("source_locations"),
    }, "entity": entity}
    return [
        {"role": "system", "content": instruction},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in proposal response")
    return json.loads(text[start:end + 1])


def propose_operations(
    audit_results: list,
    inventory: dict,
    *,
    generate: Callable[[list[dict]], str] | None = None,
) -> dict:
    """Generate validated revision operations from ``requires_revision`` audits.

    When ``generate`` is None (no LLM), no operations are proposed and each
    actionable finding is reported as a manual-review item — never auto-confirmed.
    """
    inventory = inventory or {}
    targets = [a for a in (audit_results or []) if a.get("requires_revision")]
    operations, rejected, manual_review = [], [], []
    for audit in targets:
        if generate is None:
            manual_review.append({
                "checkpoint_id": audit.get("checkpoint_id"),
                "target_type": audit.get("target_type"),
                "target_id": audit.get("target_id"),
                "reason": "no_llm_available_manual_operation_required",
            })
            continue
        try:
            raw = _extract_json(generate(_proposal_messages(audit, inventory)))
        except Exception as exc:
            rejected.append({"checkpoint_id": audit.get("checkpoint_id"),
                             "reason": f"proposal_generation_failed:{exc}"})
            continue
        op, reason = validate_proposed_operation(raw, inventory, audit)
        if op is not None:
            operations.append(op)
        else:
            rejected.append({"checkpoint_id": audit.get("checkpoint_id"), "reason": reason})
    return {"operations": operations, "rejected": rejected, "manual_review": manual_review}
