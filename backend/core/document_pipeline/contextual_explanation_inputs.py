"""Input construction for the ``contextual_explanation`` pipeline stage.

Design: ``docs/features/hierarchical_context_explanation_design.md`` §5.1
(Phase 2). This module turns already-persisted-or-artifact-only pipeline
state (components / claims / equations / figures / thesis) into
``ElementExplanationInput`` objects for ``ContextualExplanationAgent`` —
**all opaque ids resolved into text** before the agent ever sees them
(design principle E4: structure stays derived elsewhere, only the
explanation *text* is generated/stored by the agent).

Element id conventions (see migration 056's comment for the aspirational
shape, and the deviation noted below):

- ``figure``: ``document_figures.id`` (a real DB UUID) — figures are
  persisted early in the pipeline (``figure_image_extraction``, stage 2),
  long before this stage runs, so the DB id is already resolvable here.
- ``equation``: the artifact-level ``equation_id`` (e.g. ``"eq_3"``) —
  equations have no dedicated DB table (migration 056's own comment
  acknowledges this), so the artifact id *is* the canonical id.
- ``theory_component`` / ``theory_claim``: the **agent-level** id
  (``ComponentRecord.component_id`` / ``ClaimObjectRecord.claim_id``), NOT
  the eventual ``theory_components.id`` / ``theory_claims.id`` DB UUID.
  This is a deliberate, documented deviation from migration 056's comment:
  this stage is registered *before* ``persist_claims_components_graph``
  (per the design's §5.1 stage placement — "component_graph の後・
  course_mapping の前" — which runs long before claims/components are
  persisted to their DB tables with fresh ``uuid_generate_v4()`` ids).
  Consumers that need the DB row (a future approval API, C-layer bridging,
  etc.) can resolve the agent id to the persisted row the same way the rest
  of the codebase already does: via ``source_scope.legacy_ids`` /
  ``theory_claims`` created from the same span, matching the established
  "legacy id" resolution pattern (see ``persistence.py``'s
  ``source_scope["legacy_ids"] = [component.component_id]`` and
  ``_claim_legacy_keys``).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Callable

from episteme_graph.agents.contextual_explanation.schema import ElementExplanationInput

from .persistence import _claim_thesis_ref_index, _thesis_ref_nodes

logger = logging.getLogger(__name__)

ELEMENT_TYPE_COMPONENT = "theory_component"
ELEMENT_TYPE_CLAIM = "theory_claim"
ELEMENT_TYPE_EQUATION = "equation"
ELEMENT_TYPE_FIGURE = "figure"

# Design §5.1: "各テキストは適度に切り詰め（≤400字目安）".
MAX_TEXT_CHARS = 400
# Alignment items are a lower-priority enrichment; cap so one figure with a
# very long observation log doesn't crowd out its other lower-context entries.
MAX_ALIGNMENT_ITEMS = 8
MAX_INNER_LABELS = 10

SKIP_REASON_NO_CONCEPT_LINK = "no_concept_link"


def _clip(text: Any, limit: int = MAX_TEXT_CHARS) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _as_dict(obj: Any) -> dict:
    """Normalize a dataclass-or-plain-dict into a plain dict.

    ``figure_table_semantics`` is the one artifact in this pipeline whose
    result lacks a ``from_dict`` (see ``orchestrator._from_agent_dict``'s
    comment), so a *resumed* run hands this stage a raw dict for
    ``ctx.fig_tbl`` instead of a ``FigureTableSemanticsResult`` dataclass.
    Every other input this module reads (components / claims / equations /
    thesis) always round-trips through a real ``from_dict`` and can be
    accessed with plain ``getattr`` — this helper exists only for the
    fig_tbl path.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        try:
            return obj.to_dict()
        except Exception:  # noqa: BLE001 — fall through to dataclass/attr paths
            pass
    if is_dataclass(obj):
        return asdict(obj)
    return {}


# ---------------------------------------------------------------------------
# Cross-reference maps (agent id -> record), built once per document.
# ---------------------------------------------------------------------------


def build_component_map(component_result: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for comp in getattr(component_result, "components", []) or []:
        cid = str(getattr(comp, "component_id", "") or "")
        if cid:
            out[cid] = comp
    return out


def build_claim_map(claim_objects: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for claim in getattr(claim_objects, "claims", []) or []:
        cid = str(getattr(claim, "claim_id", "") or "")
        if cid:
            out[cid] = claim
    return out


def build_equation_map(equations: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for eq in getattr(equations, "equations", []) or []:
        eid = str(getattr(eq, "equation_id", "") or "")
        if eid:
            out[eid] = eq
    return out


def build_thesis_ref_text_map(thesis: Any) -> dict[str, dict]:
    """``thesis_ref`` string (e.g. ``"central_thesis"``) -> ``{"text","kind"}``."""
    out: dict[str, dict] = {}
    for node in _thesis_ref_nodes(thesis):
        out[node["thesis_ref"]] = {"text": node.get("text", ""), "kind": node.get("kind", "thesis")}
    return out


def build_figure_db_maps(document_id: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """``document_figures`` rows keyed by ``figure_key`` and ``caption_block_id``.

    Figures are persisted in the ``figure_image_extraction`` stage (well
    before this one runs), so these rows — including the real DB ``id`` and
    ``inner_labels`` — are always available here, fresh run or resumed.
    """
    try:
        from .figure_images import load_document_figures
    except Exception:  # noqa: BLE001
        logger.warning(
            "contextual_explanation: could not import figure_images (non-fatal)",
            exc_info=True,
        )
        return {}, {}
    try:
        rows = load_document_figures(document_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "contextual_explanation: failed to load document_figures document=%s (non-fatal)",
            document_id, exc_info=True,
        )
        return {}, {}
    by_key: dict[str, dict] = {}
    by_caption_block: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("figure_key") or "")
        if key:
            by_key[key] = row
        cbid = str(row.get("caption_block_id") or "")
        if cbid:
            by_caption_block[cbid] = row
    return by_key, by_caption_block


def build_apparatus_map(apparatus_result: Any) -> dict[str, Any]:
    """``document_figures.id`` -> ``ApparatusRecord``.

    ``ApparatusRecord.figure_id`` is set by the orchestrator's
    ``_build_apparatus_semantics`` to ``document_figures.id`` (falling back
    to ``figure_key`` only when the DB row id was unavailable), so this maps
    on the same key space as :func:`build_figure_db_maps`'s rows.
    """
    out: dict[str, Any] = {}
    for record in getattr(apparatus_result, "apparatus_records", []) or []:
        fid = str(getattr(record, "figure_id", "") or "")
        if fid:
            out[fid] = record
    return out


# ---------------------------------------------------------------------------
# L-layer generic-explanation excerpt (E3: only ever supplied for elements
# with a CONFIRMED identity link — never invented from the model's own
# background knowledge).
# ---------------------------------------------------------------------------


def build_identity_link_map(document_id: str) -> dict[tuple[str, str], str]:
    """``(element_type, element_id) -> shared_part_id`` for confirmed links only.

    Fetched once per document (not once per element) to avoid N DB round
    trips. Fail-soft: any error yields an empty map (no generic explanations
    get generated, which is the safe default per E3).
    """
    try:
        from core.deliberation.identity_links import confirmed_links_for_document
    except Exception:  # noqa: BLE001
        return {}
    try:
        links = confirmed_links_for_document(document_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "contextual_explanation: identity link lookup failed document=%s (non-fatal)",
            document_id, exc_info=True,
        )
        return {}
    out: dict[tuple[str, str], str] = {}
    for link in links or []:
        element_type = str(link.get("instance_element_type") or "")
        element_id = str(link.get("instance_element_id") or "")
        shared_part_id = str(link.get("shared_part_id") or "")
        if element_type and element_id and shared_part_id:
            out[(element_type, element_id)] = shared_part_id
    return out


def _load_library_excerpt(shared_part_id: str) -> dict | None:
    try:
        from core.library import schema as library_schema
        from core.library import store as library_store
    except Exception:  # noqa: BLE001
        return None
    try:
        entry = library_store.get_entry(shared_part_id)
        if not entry or entry.get("status") == library_schema.STATUS_RETIRED:
            return None
        version_no = int(entry.get("latest_version_no") or 0)
        if version_no <= 0:
            return None
        version = library_store.get_version(shared_part_id, version_no)
        if not version:
            return None
        content = version.get("content") or {}
        return {
            "entry_id": shared_part_id,
            "version_no": version_no,
            "name": str(content.get("name") or ""),
            "summary": _clip(content.get("summary") or ""),
            "body_excerpt": _clip(json.dumps(content.get("body") or {}, ensure_ascii=False)),
        }
    except Exception:  # noqa: BLE001
        logger.warning(
            "contextual_explanation: library excerpt resolution failed for entry=%s (non-fatal)",
            shared_part_id, exc_info=True,
        )
        return None


def make_library_resolver(document_id: str) -> Callable[[str, str], dict | None]:
    """Build a ``(element_type, element_id) -> library_excerpt|None`` resolver.

    Caches per ``shared_part_id`` within one document build so elements that
    share the same L-layer entry don't each re-fetch it.
    """
    identity_link_map = build_identity_link_map(document_id)
    cache: dict[str, dict | None] = {}

    def resolve(element_type: str, element_id: str) -> dict | None:
        shared_part_id = identity_link_map.get((element_type, element_id))
        if not shared_part_id:
            return None
        if shared_part_id not in cache:
            cache[shared_part_id] = _load_library_excerpt(shared_part_id)
        return cache[shared_part_id]

    return resolve


# ---------------------------------------------------------------------------
# Equation text helper (mirrors persistence.py's _equation_previews preference
# order: reconstruction wins over source_extraction once it has a real status).
# ---------------------------------------------------------------------------


def _equation_display_text(record: Any) -> str:
    src = getattr(record, "source_extraction", None)
    rec = getattr(record, "reconstruction", None)
    use_reconstruction = bool(rec and getattr(rec, "status", "none") != "none")
    latex = None
    plain = None
    if use_reconstruction:
        latex = getattr(rec, "latex", None)
        plain = getattr(rec, "plain_text", None)
    elif src:
        latex = getattr(src, "latex", None)
        plain = getattr(src, "plain_text", None)
    raw = getattr(src, "raw_text", "") if src else ""
    return str(latex or plain or raw or "").strip()


# ---------------------------------------------------------------------------
# Per-element-type builders
# ---------------------------------------------------------------------------


def _component_upper_context(comp: Any, thesis_ref_map: dict, claim_map: dict) -> list[dict]:
    upper: list[dict] = []
    for ref in getattr(comp, "supports_thesis_node_ids", []) or []:
        node = thesis_ref_map.get(str(ref))
        text = (node or {}).get("text") if node else ""
        if text:
            upper.append({"relation": "supports", "text": _clip(text), "kind": "thesis"})
    role = str(getattr(comp, "role_in_thesis", "") or "").strip()
    if role:
        upper.append({"relation": "role_in_thesis", "text": _clip(role), "kind": "thesis"})
    for claim_id in getattr(comp, "supports_claim_ids", []) or []:
        claim = claim_map.get(str(claim_id))
        text = str(getattr(claim, "text", "") or "").strip() if claim is not None else ""
        if text:
            upper.append({"relation": "supports_claim", "text": _clip(text), "kind": "claim"})
    return upper


def _component_lower_context(comp: Any, component_map: dict, equation_map: dict) -> list[dict]:
    lower: list[dict] = []
    for dep in getattr(comp, "dependencies", []) or []:
        if not isinstance(dep, dict):
            continue
        dep_type = str(dep.get("dependency_type") or "depends_on")
        for ref in dep.get("component_refs") or []:
            member = component_map.get(str(ref))
            if member is None:
                continue
            label = str(getattr(member, "label", "") or "").strip()
            summary = str(getattr(member, "summary", "") or "").strip()
            text = f"{label}: {summary}" if label and summary else (label or summary)
            if text:
                lower.append({"relation": dep_type, "text": _clip(text), "kind": "component"})
    for eq_id in getattr(comp, "linked_equation_ids", []) or []:
        eq = equation_map.get(str(eq_id))
        if eq is None:
            continue
        summary = str(getattr(getattr(eq, "semantics", None), "summary", "") or "").strip()
        if summary:
            lower.append({"relation": "uses_equation", "text": _clip(summary), "kind": "equation"})
    return lower


def build_component_element(
    comp: Any,
    *,
    thesis_ref_map: dict,
    claim_map: dict,
    component_map: dict,
    equation_map: dict,
    resolve_library: Callable[[str, str], dict | None],
) -> ElementExplanationInput:
    element_id = str(getattr(comp, "component_id", "") or "")
    label = str(getattr(comp, "label", "") or "").strip()
    summary = str(getattr(comp, "summary", "") or "").strip()
    local_text = f"{label}: {summary}" if label and summary else (label or summary)
    return ElementExplanationInput(
        element_type=ELEMENT_TYPE_COMPONENT,
        element_id=element_id,
        local_text=_clip(local_text),
        upper_context=_component_upper_context(comp, thesis_ref_map, claim_map),
        lower_context=_component_lower_context(comp, component_map, equation_map),
        library_excerpt=resolve_library(ELEMENT_TYPE_COMPONENT, element_id),
    )


def _claim_lower_context(claim: Any, claim_map: dict, equation_map: dict) -> list[dict]:
    lower: list[dict] = []
    for sub_id in getattr(claim, "subclaim_ids", []) or []:
        sub = claim_map.get(str(sub_id))
        text = str(getattr(sub, "text", "") or "").strip() if sub is not None else ""
        if text:
            lower.append({"relation": "atomic_subclaim", "text": _clip(text), "kind": "claim"})
    for eq_id in getattr(claim, "equation_ids", []) or []:
        eq = equation_map.get(str(eq_id))
        if eq is None:
            continue
        summary = str(getattr(getattr(eq, "semantics", None), "summary", "") or "").strip()
        if summary:
            lower.append({"relation": "uses_equation", "text": _clip(summary), "kind": "equation"})
    return lower


def _thesis_upper_entries(claim_id: str, thesis_ref_index: dict[str, list[dict]]) -> list[dict]:
    upper: list[dict] = []
    for ref_entry in thesis_ref_index.get(claim_id, []) or []:
        excerpt = ref_entry.get("text_excerpt") or ""
        if excerpt:
            upper.append({"relation": "supports", "text": _clip(excerpt), "kind": "thesis"})
    return upper


def build_claim_element(
    claim: Any,
    *,
    claim_map: dict,
    equation_map: dict,
    thesis_ref_index: dict[str, list[dict]],
    resolve_library: Callable[[str, str], dict | None],
) -> ElementExplanationInput:
    claim_id = str(getattr(claim, "claim_id", "") or "")
    text = str(getattr(claim, "text", "") or "").strip()
    upper = _thesis_upper_entries(claim_id, thesis_ref_index)
    section_title = str(getattr(claim, "section_title", "") or "").strip()
    if section_title:
        upper.append({"relation": "located_in", "text": _clip(section_title), "kind": "section"})
    return ElementExplanationInput(
        element_type=ELEMENT_TYPE_CLAIM,
        element_id=claim_id,
        local_text=_clip(text),
        upper_context=upper,
        lower_context=_claim_lower_context(claim, claim_map, equation_map),
        library_excerpt=resolve_library(ELEMENT_TYPE_CLAIM, claim_id),
    )


def _equation_upper_context(eq: Any, claim_map: dict, thesis_ref_index: dict[str, list[dict]]) -> list[dict]:
    upper: list[dict] = []
    semantics = getattr(eq, "semantics", None)
    linked_claim_ids = list(getattr(semantics, "linked_claim_ids", []) or []) if semantics else []
    for claim_id in linked_claim_ids:
        claim_id = str(claim_id)
        claim = claim_map.get(claim_id)
        text = str(getattr(claim, "text", "") or "").strip() if claim is not None else ""
        if text:
            upper.append({"relation": "supports_claim", "text": _clip(text), "kind": "claim"})
        upper.extend(_thesis_upper_entries(claim_id, thesis_ref_index))
    return upper


def _equation_lower_context(eq: Any, equation_map: dict) -> list[dict]:
    lower: list[dict] = []
    semantics = getattr(eq, "semantics", None)
    for symbol in (getattr(semantics, "defined_symbols", []) or []) if semantics else []:
        meaning = str(getattr(symbol, "meaning", "") or "").strip()
        if not meaning:
            continue
        name = str(getattr(symbol, "symbol", "") or "").strip()
        text = f"{name}: {meaning}" if name else meaning
        lower.append({"relation": "defines_symbol", "text": _clip(text), "kind": "symbol"})
    for input_eq_id in (getattr(semantics, "input_equation_ids", []) or []) if semantics else []:
        input_eq = equation_map.get(str(input_eq_id))
        if input_eq is None:
            continue
        input_summary = str(getattr(getattr(input_eq, "semantics", None), "summary", "") or "").strip()
        text = input_summary or _equation_display_text(input_eq)
        if text:
            lower.append({"relation": "derived_from", "text": _clip(text), "kind": "equation"})
    return lower


def build_equation_element(
    eq: Any,
    *,
    claim_map: dict,
    equation_map: dict,
    thesis_ref_index: dict[str, list[dict]],
    resolve_library: Callable[[str, str], dict | None],
) -> ElementExplanationInput:
    equation_id = str(getattr(eq, "equation_id", "") or "")
    semantics = getattr(eq, "semantics", None)
    summary = str(getattr(semantics, "summary", "") or "").strip() if semantics else ""
    eq_text = _equation_display_text(eq)
    local_text = f"{summary}\n{eq_text}".strip() if (summary and eq_text) else (summary or eq_text)
    return ElementExplanationInput(
        element_type=ELEMENT_TYPE_EQUATION,
        element_id=equation_id,
        local_text=_clip(local_text),
        upper_context=_equation_upper_context(eq, claim_map, thesis_ref_index),
        lower_context=_equation_lower_context(eq, equation_map),
        library_excerpt=resolve_library(ELEMENT_TYPE_EQUATION, equation_id),
    )


def _resolve_figure_db_row(
    fig_dict: dict,
    figure_rows_by_key: dict[str, dict],
    figure_rows_by_caption_block: dict[str, dict],
) -> dict | None:
    source_location = fig_dict.get("source_location") or {}
    caption_block_id = str(source_location.get("caption_block_id") or "")
    row = figure_rows_by_caption_block.get(caption_block_id) if caption_block_id else None
    if row is None:
        row = figure_rows_by_key.get(str(fig_dict.get("figure_id") or ""))
    return row


def _figure_lower_context(apparatus_record: Any, inner_labels: list) -> list[dict]:
    lower: list[dict] = []
    if apparatus_record is not None:
        for part in getattr(apparatus_record, "parts", []) or []:
            name = str(getattr(part, "name", "") or "").strip()
            role = str(getattr(part, "role", "") or "").strip()
            text = f"{name}: {role}" if name and role else (name or role)
            if text:
                lower.append({"relation": "composed_of", "text": _clip(text), "kind": "apparatus_part"})
        iterative = getattr(apparatus_record, "iterative_analysis", None)
        alignment_items = list(getattr(iterative, "alignment_items", []) or []) if iterative else []
        for item in alignment_items[:MAX_ALIGNMENT_ITEMS]:
            label = str(getattr(item, "label", "") or "").strip()
            if not label:
                continue
            status = str(getattr(item, "status", "") or "").strip()
            text = f"{label} ({status})" if status else label
            lower.append({"relation": "observed", "text": _clip(text), "kind": "alignment"})
    for label_item in (inner_labels or [])[:MAX_INNER_LABELS]:
        text = str((label_item or {}).get("text") or "").strip() if isinstance(label_item, dict) else ""
        if text:
            lower.append({"relation": "labeled", "text": _clip(text), "kind": "inner_label"})
    return lower


def build_figure_element(
    fig: Any,
    *,
    claim_map: dict,
    thesis_ref_index: dict[str, list[dict]],
    apparatus_map: dict,
    figure_rows_by_key: dict[str, dict],
    figure_rows_by_caption_block: dict[str, dict],
    resolve_library: Callable[[str, str], dict | None],
) -> tuple[ElementExplanationInput | None, str | None]:
    """Returns ``(element, None)`` or ``(None, element_id)`` when skipped.

    P4 / design §5.1: a figure with no ``linked_claim_ids`` (no mention
    cross-link back to a claim — see ``figure_concept_linking_design.md``)
    is not force-fit into a fabricated position in the paper; it is skipped
    with reason ``no_concept_link`` and recorded, never silently dropped.
    """
    fig_dict = _as_dict(fig)
    linked_claim_ids = [str(c) for c in (fig_dict.get("linked_claim_ids") or [])]
    row = _resolve_figure_db_row(fig_dict, figure_rows_by_key, figure_rows_by_caption_block)
    figure_db_id = str(row.get("id")) if row else str(fig_dict.get("figure_id") or "")

    if not linked_claim_ids:
        return None, figure_db_id

    upper: list[dict] = []
    for claim_id in linked_claim_ids:
        claim = claim_map.get(claim_id)
        text = str(getattr(claim, "text", "") or "").strip() if claim is not None else ""
        if text:
            upper.append({"relation": "supports_claim", "text": _clip(text), "kind": "claim"})
        upper.extend(_thesis_upper_entries(claim_id, thesis_ref_index))

    apparatus_record = apparatus_map.get(figure_db_id)
    inner_labels = row.get("inner_labels") if row else None
    lower = _figure_lower_context(apparatus_record, inner_labels or [])

    caption = str(fig_dict.get("caption") or "").strip()
    element = ElementExplanationInput(
        element_type=ELEMENT_TYPE_FIGURE,
        element_id=figure_db_id,
        local_text=_clip(caption),
        upper_context=upper,
        lower_context=lower,
        library_excerpt=resolve_library(ELEMENT_TYPE_FIGURE, figure_db_id),
    )
    return element, None


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def build_contextual_explanation_inputs(
    *,
    document_id: str,
    cartridge_id: str | None,
    component_result: Any,
    claim_objects: Any,
    equations: Any,
    fig_tbl: Any,
    apparatus_result: Any,
    thesis: Any,
    max_elements: int,
) -> tuple[list[ElementExplanationInput], dict]:
    """Build the (priority-ordered, capped) element list + a stage_outputs meta dict.

    Priority order (design §5.1 "選抜と上限"): component -> figure ->
    claim (thesis-anchored first) -> equation. ``max_elements`` caps the
    combined list; anything beyond the cap is recorded as ``truncated`` in
    the returned meta, never silently lost from the count (P4).
    """
    del cartridge_id  # not needed for input construction; kept for call-site symmetry

    component_map = build_component_map(component_result)
    claim_map = build_claim_map(claim_objects)
    equation_map = build_equation_map(equations)
    thesis_ref_map = build_thesis_ref_text_map(thesis)
    thesis_ref_index = _claim_thesis_ref_index(thesis)
    resolve_library = make_library_resolver(document_id)

    component_elements = [
        build_component_element(
            comp,
            thesis_ref_map=thesis_ref_map,
            claim_map=claim_map,
            component_map=component_map,
            equation_map=equation_map,
            resolve_library=resolve_library,
        )
        for comp in getattr(component_result, "components", []) or []
        if str(getattr(comp, "component_id", "") or "")
    ]

    figure_rows_by_key, figure_rows_by_caption_block = build_figure_db_maps(document_id)
    apparatus_map = build_apparatus_map(apparatus_result)
    figure_elements: list[ElementExplanationInput] = []
    skipped: list[dict] = []
    for fig in (_as_dict(fig_tbl).get("figures") or []):
        element, skipped_id = build_figure_element(
            fig,
            claim_map=claim_map,
            thesis_ref_index=thesis_ref_index,
            apparatus_map=apparatus_map,
            figure_rows_by_key=figure_rows_by_key,
            figure_rows_by_caption_block=figure_rows_by_caption_block,
            resolve_library=resolve_library,
        )
        if element is not None:
            figure_elements.append(element)
        else:
            skipped.append({
                "element_type": ELEMENT_TYPE_FIGURE,
                "element_id": skipped_id or "",
                "reason": SKIP_REASON_NO_CONCEPT_LINK,
            })

    claim_elements_thesis: list[ElementExplanationInput] = []
    claim_elements_other: list[ElementExplanationInput] = []
    for claim in getattr(claim_objects, "claims", []) or []:
        claim_id = str(getattr(claim, "claim_id", "") or "")
        if not claim_id:
            continue
        element = build_claim_element(
            claim,
            claim_map=claim_map,
            equation_map=equation_map,
            thesis_ref_index=thesis_ref_index,
            resolve_library=resolve_library,
        )
        if thesis_ref_index.get(claim_id):
            claim_elements_thesis.append(element)
        else:
            claim_elements_other.append(element)
    claim_elements = claim_elements_thesis + claim_elements_other

    equation_elements = [
        build_equation_element(
            eq,
            claim_map=claim_map,
            equation_map=equation_map,
            thesis_ref_index=thesis_ref_index,
            resolve_library=resolve_library,
        )
        for eq in getattr(equations, "equations", []) or []
        if str(getattr(eq, "equation_id", "") or "")
    ]

    combined = component_elements + figure_elements + claim_elements + equation_elements
    # max_elements=0 means "select zero elements" (a hard operator override,
    # matching apparatus_semantics's max-images-per-document convention), not
    # "cap disabled" — so this must not special-case 0 as unlimited.
    truncated = len(combined) > max_elements
    elements = combined[:max_elements] if truncated else combined

    meta = {
        "considered": len(combined),
        "selected": len(elements),
        "truncated": truncated,
        "truncated_count": max(0, len(combined) - len(elements)),
        "counts_by_kind": {
            "component": len(component_elements),
            "figure": len(figure_elements),
            "claim": len(claim_elements),
            "equation": len(equation_elements),
        },
        "skipped": skipped,
    }
    return elements, meta
