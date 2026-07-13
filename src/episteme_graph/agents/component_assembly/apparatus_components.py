"""Deterministic construction of apparatus candidate components (§5-5).

Design doc: docs/features/image_pipeline_knowledge_library_design.md §5-5.

Converts an ``ApparatusSemanticsResult`` (vision-LLM apparatus/instrument
identification — always candidate-only, see
``episteme_graph.agents.apparatus_semantics.schema``) into
``ComponentRecord``\\ s using the exact same schema
``ComponentAssemblyAgent`` already produces for claim/equation-derived
components, so downstream consumers (persistence, export gate) do not need a
second code path.

This module is entirely **non-LLM / deterministic**: no interpretation
happens here, only structural translation. Design principle #2 ("画像の意味
解釈は candidate 止まり、確定は人間") is preserved by never assigning a
confirmed ``review_status`` / ``maturity_source`` / ``publish_ready``, and by
never inventing a ``component_type`` other than the literal ``'apparatus'``
(matching the ``theory_components.component_type`` CHECK vocabulary added by
migration 041 — see ``backend/db/041_image_pipeline.sql``).

v1 scope (design doc §5-5): one ``ApparatusRecord`` -> one component
(``component_type='apparatus'``). Parts/connections are folded into the
component's own ``inputs`` / ``internal_flow`` fields; no per-part child
component is created (kept minimal by design).

**Not exercised in v1** (design doc §5-5: "TheoryOperationGraph には組み込ま
ない"): apparatus components intentionally carry no
``linked_derivation_ids`` / ``linked_equation_ids`` / claim links, so
``component_graph/normalizer.py`` (which links components to graph nodes only
through those fields) never pulls them into the main/equation_detail graph.
"""
from __future__ import annotations

from episteme_graph.agents.apparatus_semantics.schema import (
    ApparatusConnection,
    ApparatusPart,
    ApparatusRecord,
    ApparatusSemanticsResult,
)

from .schema import CartridgeContext, ComponentRecord

# Matches CORE_COMPONENT_TYPES in schema.py and the cartridge component_types.json
# entry added alongside it, and the DB CHECK vocabulary from migration 041.
APPARATUS_COMPONENT_TYPE = "apparatus"


def build_apparatus_components(
    apparatus_semantics: ApparatusSemanticsResult | dict | None,
    document_id: str,
    cartridge: CartridgeContext | None = None,
    existing_component_ids: set[str] | None = None,
) -> list[ComponentRecord]:
    """Deterministically build apparatus candidate components.

    ``apparatus_semantics`` may be an ``ApparatusSemanticsResult`` instance or
    an equivalent plain ``dict`` (both round-trip through
    ``ApparatusSemanticsResult.from_dict`` / ``to_dict``). Returns ``[]`` when
    there is nothing to build (``None`` input, or a result with no
    ``apparatus_records``) — this is the normal "apparatus_semantics stage
    was skipped/absent" case, not an error.

    ``existing_component_ids`` lets the caller (``ComponentAssemblyAgent.run``)
    pass the IDs already used by claim/equation-derived components so the new
    ``apparatus_*`` IDs never collide with them.

    Every ``ApparatusRecord`` present is kept, including ``match_status ==
    'unknown'`` ones (P4, "情報を落とさない") — an unidentified apparatus
    still becomes a reviewable component with a neutral label, it is never
    silently dropped.
    """
    result = _coerce_result(apparatus_semantics)
    if result is None:
        return []

    used_ids: set[str] = set(existing_component_ids or ())
    components: list[ComponentRecord] = []
    for idx, record in enumerate(result.apparatus_records or [], start=1):
        component_id = _unique_component_id(record, idx, used_ids)
        used_ids.add(component_id)
        components.append(
            _component_from_record(record, component_id, document_id, cartridge)
        )
    return components


def _coerce_result(
    apparatus_semantics: ApparatusSemanticsResult | dict | None,
) -> ApparatusSemanticsResult | None:
    if apparatus_semantics is None:
        return None
    if isinstance(apparatus_semantics, ApparatusSemanticsResult):
        return apparatus_semantics
    if isinstance(apparatus_semantics, dict):
        return ApparatusSemanticsResult.from_dict(apparatus_semantics)
    # Unknown type: fail closed rather than guess a shape.
    return None


def _unique_component_id(record: ApparatusRecord, idx: int, used_ids: set[str]) -> str:
    """Derive a stable, collision-free component_id from the figure key.

    Deterministic across re-runs (same figure_key -> same id), which keeps
    diffs/persistence stable. Falls back to the record's position when no
    figure_key/figure_id is available, and to a numeric suffix on collision.
    """
    base_key = str(getattr(record, "figure_key", "") or getattr(record, "figure_id", "") or "").strip()
    slug = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in base_key.lower()).strip("_")
    slug = slug or f"{idx:03d}"
    candidate = f"apparatus_{slug}"
    if candidate not in used_ids:
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in used_ids:
        suffix += 1
    return f"{candidate}_{suffix}"


def _apparatus_label(record: ApparatusRecord) -> str:
    name = str(getattr(record, "apparatus_name_candidate", "") or "").strip()
    if name:
        return name
    # P4: an apparatus the LLM could not identify (match_status='unknown')
    # is still held as a reviewable record, with a neutral, non-asserting
    # label rather than being dropped.
    locator = str(getattr(record, "figure_key", "") or getattr(record, "figure_id", "") or "").strip() or "?"
    return f"Unidentified apparatus ({locator})"


def _parts_to_inputs(parts: list[ApparatusPart] | None) -> list[dict]:
    """Fold apparatus parts into the component's ``inputs`` field.

    Mirrors the existing ``inputs`` shape (``name`` + free-form descriptive
    keys, see e.g. component_assembly/examples/sample_output.json) rather
    than inventing a new field on ``ComponentRecord``.
    """
    inputs: list[dict] = []
    for i, part in enumerate(parts or []):
        inputs.append({
            "name": str(getattr(part, "name", "") or f"part_{i + 1}"),
            "text": str(getattr(part, "role", "") or ""),
            "role": str(getattr(part, "role", "") or ""),
            "evidence_quote": str(getattr(part, "evidence_quote", "") or ""),
            "reason": str(getattr(part, "reason", "") or ""),
            "confidence": float(getattr(part, "confidence", 0.0) or 0.0),
        })
    return inputs


def _connections_to_internal_flow(connections: list[ApparatusConnection] | None) -> list[dict]:
    """Fold apparatus connections into the component's ``internal_flow`` field.

    Uses the existing ``from`` / ``relation`` / ``to`` internal_flow step
    shape the validator already understands (see
    ``ComponentAssemblyValidator._check_internal_flow``).
    """
    flow: list[dict] = []
    for conn in connections or []:
        flow.append({
            "from": str(getattr(conn, "from_part", "") or ""),
            "relation": str(getattr(conn, "relation", "") or ""),
            "to": str(getattr(conn, "to_part", "") or ""),
            "reason": str(getattr(conn, "reason", "") or ""),
            "confidence": float(getattr(conn, "confidence", 0.0) or 0.0),
        })
    return flow


def _summary_for_record(record: ApparatusRecord, label: str) -> str:
    reason = str(getattr(record, "reason", "") or "").strip()
    if reason:
        return reason
    parts_summary = ", ".join(
        str(getattr(p, "name", "") or "").strip()
        for p in (getattr(record, "parts", None) or [])
        if str(getattr(p, "name", "") or "").strip()
    )
    locator = str(getattr(record, "figure_key", "") or getattr(record, "figure_id", "") or "?")
    summary = (
        f"Apparatus candidate ({label}) identified from figure {locator} "
        f"(match_status={getattr(record, 'match_status', 'unknown')})."
    )
    if parts_summary:
        summary += f" Parts: {parts_summary}."
    return summary


def _component_from_record(
    record: ApparatusRecord,
    component_id: str,
    document_id: str,
    cartridge: CartridgeContext | None,
) -> ComponentRecord:
    label = _apparatus_label(record)
    review_notes = (
        ["apparatus identification could not be repaired by "
         "ApparatusSemanticsAgent; kept as an unknown/reviewable candidate (P4)"]
        if getattr(record, "repair_failed", False) else []
    )
    return ComponentRecord(
        component_id=component_id,
        component_type=APPARATUS_COMPONENT_TYPE,
        label=label,
        summary=_summary_for_record(record, label),
        inputs=_parts_to_inputs(record.parts),
        outputs=[],
        preconditions=[],
        cautions=[],
        dependencies=[],
        # Standard evidence_refs shape (see component_assembly/examples);
        # apparatus candidates carry no claim/equation/evidence links in v1
        # (they are figure-derived, not claim-derived) -- kept empty rather
        # than omitted so downstream code relying on these keys existing
        # behaves the same as for any other component.
        evidence_refs={
            "claim_ids": [],
            "evidence_ids": [],
            "equation_ids": [],
            "thesis_refs": [],
            "dsl_refs": {"node_ids": [], "edge_ids": []},
        },
        reason=str(getattr(record, "reason", "") or ""),
        confidence=float(getattr(record, "confidence", 0.0) or 0.0),
        review_notes=review_notes,
        internal_flow=_connections_to_internal_flow(record.connections),
        # Design principle #2: vision-derived identification is never
        # auto-confirmed. "teacher_review_required" is the component-level
        # vocabulary's review_required family (see
        # backend/cartridges/particle_physics/support_statuses.json),
        # matching apparatus_semantics' own REVIEW_STATUS_DEFAULT.
        review_status="teacher_review_required",
        source_scope={
            "source": "apparatus_semantics",
            "document_id": document_id,
            "cartridge_id": getattr(cartridge, "cartridge_id", None),
            "figure_id": getattr(record, "figure_id", None),
            "figure_key": getattr(record, "figure_key", None),
            "match_status": getattr(record, "match_status", None),
            "matched_library_entry_id": getattr(record, "matched_library_entry_id", None),
            "matched_library_version_no": getattr(record, "matched_library_version_no", None),
            "evidence_quote": getattr(record, "evidence_quote", ""),
            "source_backing_status": getattr(record, "source_backing_status", None),
            "repair_failed": getattr(record, "repair_failed", False),
        },
        # No theory-operation classification: apparatus components are
        # intentionally not derivation-like (design doc §5-5, "式backing が
        # 無い"), so no operation is declared and they never trip the
        # derivation-path checks in ComponentAssemblyValidator.
        operation="",
        maturity_source="unknown" if getattr(record, "repair_failed", False) else "llm_proposed",
        publish_ready=False,
    )
