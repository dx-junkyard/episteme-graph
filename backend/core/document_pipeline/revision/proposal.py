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


def _chunk_ids_from_locations(value: object) -> set[str]:
    """Collect chunk ids referenced by a list of source-location dicts."""
    out: set[str] = set()
    for loc in value or []:
        if isinstance(loc, dict):
            cid = loc.get("chunk_id")
            if cid:
                out.add(str(cid))
        elif loc:
            out.add(str(loc))
    return out


def trusted_chunk_ids_for_audit_result(audit_result: dict | None) -> set[str]:
    """Server-trusted source chunk ids for one audit result.

    Only the chunks the deterministic retrieval actually returned — recorded in
    ``source_locations`` — are trusted as proof of existence (the ``chunks.id``
    space). The LLM-returned ``source_chunk_ids`` are deliberately NOT used here:
    an LLM's own output must never be the sole evidence that a chunk exists
    (#414-5). They are validated against this trusted set instead.
    """
    audit_result = audit_result or {}
    return _chunk_ids_from_locations(audit_result.get("source_locations"))


# Back-compat alias (older callers); now returns only the trusted retrieval set.
chunk_ids_for_audit_result = trusted_chunk_ids_for_audit_result


def _dedup(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _resolve_evidence_refs(
    raw_op: dict,
    audit_result: dict,
    *,
    known_evidence_ids: set[str],
    known_chunk_ids: set[str],
) -> tuple[list[str], list[str], str]:
    """Split + validate references into the registry-evidence and source-chunk spaces.

    Returns ``(evidence_refs, source_chunk_ids, reason)``. ``reason`` is non-empty
    only when a reference resolves to *neither* space (a genuinely unknown id);
    valid chunk references are never reported as ``unknown_evidence`` (#412 P0-6).

    Three input shapes are accepted so the LLM/audit may emit either typed fields
    or the legacy mixed ``evidence_refs`` list:
      * ``evidence_ids``      — explicit EvidenceRegistry ids (validated vs registry)
      * ``source_chunk_ids``  — explicit ``chunks.id`` refs (validated vs chunk index)
      * ``evidence_refs``     — legacy mixed list, routed by id space
    """
    explicit_evidence = [str(e) for e in (raw_op.get("evidence_ids") or []) if e]
    explicit_chunks = [
        str(c)
        for c in (raw_op.get("source_chunk_ids") or audit_result.get("source_chunk_ids") or [])
        if c
    ]
    mixed = [
        str(e)
        for e in (raw_op.get("evidence_refs") or audit_result.get("evidence_refs") or [])
        if e
    ]

    evidence_refs: list[str] = []
    source_chunk_ids: list[str] = []
    unknown_evidence: list[str] = []
    unknown_chunks: list[str] = []

    # Every reference is validated against its own server-trusted index. Nothing
    # is accepted on faith — an empty known-chunk set means a source_chunk_id
    # cannot be proven to exist, so it is rejected (fail-closed), never accepted
    # blindly (#414-5).
    for e in explicit_evidence:
        (evidence_refs if e in known_evidence_ids else unknown_evidence).append(e)
    for c in explicit_chunks:
        (source_chunk_ids if c in known_chunk_ids else unknown_chunks).append(c)
    for ref in mixed:
        if ref in known_evidence_ids:
            evidence_refs.append(ref)
        elif ref in known_chunk_ids:
            source_chunk_ids.append(ref)
        else:
            unknown_evidence.append(ref)

    reason = ""
    if unknown_evidence:
        reason = f"unknown_evidence:{','.join(_dedup(unknown_evidence))}"
    elif unknown_chunks:
        reason = f"unknown_source_chunk:{','.join(_dedup(unknown_chunks))}"
    return _dedup(evidence_refs), _dedup(source_chunk_ids), reason


def validate_proposed_operation(
    raw_op: dict,
    inventory: dict,
    audit_result: dict | None = None,
    *,
    known_checkpoint_ids: set[str] | None = None,
    known_chunk_ids: set[str] | None = None,
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

    # References must resolve, but the registry-evidence and source-chunk ID
    # spaces are validated separately (#412 P0-6). A valid chunk reference (in
    # ``chunks.id``) is kept as ``source_chunk_ids`` and never rejected as
    # unknown registry evidence.
    known_ev = _known_evidence_ids(inventory)
    audit_result = audit_result or {}
    known_chunks = set(known_chunk_ids or set()) | chunk_ids_for_audit_result(audit_result)
    evidence_refs, source_chunk_ids, ref_reason = _resolve_evidence_refs(
        raw_op, audit_result, known_evidence_ids=known_ev, known_chunk_ids=known_chunks
    )
    if ref_reason:
        return None, ref_reason

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
    if not evidence_refs and not source_chunk_ids and not source_locations:
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
        source_chunk_ids=source_chunk_ids,
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
    known_chunk_ids: set[str] | None = None,
) -> dict:
    """Validate a batch of raw proposed operations (e.g. from the debug raw-JSON path).

    ``known_chunk_ids`` is the server-trusted source-chunk index (e.g. from the
    ``chunks`` table); it is unioned with the chunk ids the deterministic audit
    actually retrieved and with the checkpoints' source scope. The LLM's own
    ``source_chunk_ids`` are never trusted as proof of existence (#414-5).
    """
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
    # Source-chunk id space (chunks.id), distinct from EvidenceRegistry ids and
    # built only from trusted server data: the caller-provided chunk index, the
    # chunks the deterministic retrieval returned, and the checkpoints' scope.
    trusted_chunk_ids: set[str] = set(known_chunk_ids or set())
    for audit in audit_by_checkpoint.values():
        trusted_chunk_ids |= trusted_chunk_ids_for_audit_result(audit)
    for checkpoint in (checkpoints or []):
        if isinstance(checkpoint, dict):
            trusted_chunk_ids |= _chunk_ids_from_locations(checkpoint.get("source_scope"))
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
            known_chunk_ids=trusted_chunk_ids,
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
        "\"after_json\":..,\"reason\":..,"
        "\"evidence_ids\":[EvidenceRegistryのevidence_id],"
        "\"source_chunk_ids\":[原文chunkのchunks.id]}。"
        "evidence_ids（登録済みevidence）とsource_chunk_ids（原文chunk）はID空間が異なるため"
        "必ず分けて出力すること。chunk IDをevidence_idとして出さない。"
        "原文根拠を必ず付け、推測だけで確定しないこと。"
    )
    payload = {"audit": {
        "verdict": audit_result.get("verdict"),
        "findings": audit_result.get("findings"),
        "target_type": audit_result.get("target_type"),
        "target_id": audit_result.get("target_id"),
        "evidence_refs": audit_result.get("evidence_refs"),
        "source_chunk_ids": sorted(chunk_ids_for_audit_result(audit_result)),
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
    known_chunk_ids: set[str] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict:
    """Generate validated revision operations from ``requires_revision`` audits.

    When ``generate`` is None (no LLM), no operations are proposed and each
    actionable finding is reported as a manual-review item — never auto-confirmed.
    ``known_chunk_ids`` is the server-trusted source-chunk index used (together
    with each audit's retrieved chunks) to validate ``source_chunk_ids`` (#414-5).
    ``progress_callback(completed, total)`` reports per-target progress (#414-3).
    """
    inventory = inventory or {}
    trusted_chunk_ids = set(known_chunk_ids or set())
    targets = [a for a in (audit_results or []) if a.get("requires_revision")]
    total = len(targets)
    operations, rejected, manual_review = [], [], []
    for index, audit in enumerate(targets, start=1):
        if generate is None:
            manual_review.append({
                "checkpoint_id": audit.get("checkpoint_id"),
                "target_type": audit.get("target_type"),
                "target_id": audit.get("target_id"),
                "reason": "no_llm_available_manual_operation_required",
            })
            if progress_callback:
                progress_callback(index, total)
            continue
        try:
            raw = _extract_json(generate(_proposal_messages(audit, inventory)))
        except Exception as exc:
            rejected.append({"checkpoint_id": audit.get("checkpoint_id"),
                             "reason": f"proposal_generation_failed:{exc}"})
            if progress_callback:
                progress_callback(index, total)
            continue
        op, reason = validate_proposed_operation(
            raw,
            inventory,
            audit,
            known_checkpoint_ids={str(audit.get("checkpoint_id"))},
            known_chunk_ids=trusted_chunk_ids | trusted_chunk_ids_for_audit_result(audit),
        )
        if op is not None:
            operations.append(op)
        else:
            rejected.append({"checkpoint_id": audit.get("checkpoint_id"), "reason": reason})
        if progress_callback:
            progress_callback(index, total)
    return {"operations": operations, "rejected": rejected, "manual_review": manual_review}
