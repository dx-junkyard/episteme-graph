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


def validate_proposed_operation(
    raw_op: dict,
    inventory: dict,
    audit_result: dict | None = None,
    *,
    known_checkpoint_ids: set[str] | None = None,
) -> tuple:
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
    if operation in _MODIFYING and not (
        operation == "add_relation" and target_type == "graph_edge"
    ):
        if not target_id or not _entity_exists(inventory, target_type, target_id):
            return None, f"unknown_target:{target_type}:{target_id}"
    if operation == "merge_entities":
        source_ids = [
            str(value)
            for value in (raw_op.get("source_ids") or raw_op.get("target_ids") or [])
            if value
        ]
        if target_id and target_id not in source_ids:
            source_ids.insert(0, target_id)
        if len(source_ids) < 2:
            return None, "merge_requires_multiple_sources"
        for source_id in source_ids:
            if not _entity_exists(inventory, target_type, source_id):
                return None, f"unknown_source:{target_type}:{source_id}"
    if operation in {"add_relation", "remove_relation"} and target_type != "graph_edge":
        relation_target_type = raw_op.get("relation_target_type") or "evidence"
        relation_target_id = str(raw_op.get("relation_target_id") or "")
        if not relation_target_id or not _entity_exists(
            inventory, relation_target_type, relation_target_id
        ):
            return None, f"unknown_relation_target:{relation_target_type}:{relation_target_id}"

    # Evidence refs must resolve (source-derived, from the audit result).
    known_ev = _known_evidence_ids(inventory)
    audit_result = audit_result or {}
    evidence_refs = [str(e) for e in (raw_op.get("evidence_refs")
                                      or audit_result.get("evidence_refs") or []) if e]
    unknown_evidence = [e for e in evidence_refs if e not in known_ev]
    if unknown_evidence:
        return None, f"unknown_evidence:{','.join(unknown_evidence)}"

    checkpoint_ids = [str(value) for value in (raw_op.get("checkpoint_ids") or []) if value]
    if audit_result.get("checkpoint_id") and audit_result["checkpoint_id"] not in checkpoint_ids:
        checkpoint_ids.append(audit_result["checkpoint_id"])
    if not checkpoint_ids:
        return None, "missing_checkpoint"
    if known_checkpoint_ids is not None:
        unknown_checkpoints = [
            checkpoint_id
            for checkpoint_id in checkpoint_ids
            if checkpoint_id not in known_checkpoint_ids
        ]
        if unknown_checkpoints:
            return None, f"unknown_checkpoint:{','.join(unknown_checkpoints)}"

    source_locations = (
        raw_op.get("source_locations") or audit_result.get("source_locations") or []
    )
    if not evidence_refs and not source_locations:
        return None, "missing_source_or_evidence"

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
        source_locations=source_locations,
        confidence=float(raw_op.get("confidence") or audit_result.get("confidence") or 0.0),
        operation_id=raw_op.get("operation_id") or "",
        source_ids=raw_op.get("source_ids") or raw_op.get("target_ids") or [],
        relation_target_type=raw_op.get("relation_target_type"),
        relation_target_id=raw_op.get("relation_target_id"),
    )
    return op, ""


def validate_proposed_operations(
    raw_ops: list,
    inventory: dict,
    *,
    audit_results: list[dict] | None = None,
    checkpoints: list[dict] | None = None,
) -> dict:
    """Validate a batch of raw proposed operations (e.g. from the debug raw-JSON path)."""
    accepted, rejected = [], []
    audit_by_checkpoint = {
        str(audit.get("checkpoint_id")): audit
        for audit in (audit_results or [])
        if isinstance(audit, dict) and audit.get("checkpoint_id")
    }
    known_checkpoint_ids = {
        str(checkpoint.get("checkpoint_id"))
        for checkpoint in (checkpoints or [])
        if isinstance(checkpoint, dict) and checkpoint.get("checkpoint_id")
    } | set(audit_by_checkpoint)
    seen_targets: set[tuple[str, str, str]] = set()
    for raw in raw_ops or []:
        raw_checkpoint_ids = [
            str(value) for value in (raw.get("checkpoint_ids") or [])
        ] if isinstance(raw, dict) else []
        audit = next(
            (audit_by_checkpoint[value] for value in raw_checkpoint_ids if value in audit_by_checkpoint),
            None,
        )
        op, reason = validate_proposed_operation(
            raw,
            inventory,
            audit,
            known_checkpoint_ids=known_checkpoint_ids,
        )
        if op is not None:
            key = (op.get("operation") or "", op.get("target_type") or "", op.get("target_id") or "")
            if key in seen_targets:
                rejected.append({"raw": raw, "reason": "duplicate_operation"})
            else:
                seen_targets.add(key)
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
        op, reason = validate_proposed_operation(
            raw,
            inventory,
            audit,
            known_checkpoint_ids={str(audit.get("checkpoint_id"))},
        )
        if op is not None:
            operations.append(op)
        else:
            rejected.append({"checkpoint_id": audit.get("checkpoint_id"), "reason": reason})
    return {"operations": operations, "rejected": rejected, "manual_review": manual_review}
