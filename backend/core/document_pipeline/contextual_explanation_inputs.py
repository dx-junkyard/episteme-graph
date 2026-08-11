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
import re
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Iterable

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
# Bound on the reverse "which courses use this document" lookup (see
# ``build_course_snapshot_equation_ids``): a document reused by dozens of
# courses must not turn one pipeline stage into an unbounded scan.
MAX_COURSE_SNAPSHOTS = 20

SKIP_REASON_NO_CONCEPT_LINK = "no_concept_link"
# A required equation id (referenced by teaching material / evidence links)
# that has no counterpart in this document's ``equation_semantics`` artifact.
# Recorded, never fabricated into an input (指示書 §5.2).
SKIP_REASON_EQUATION_NOT_RESOLVED = "equation_not_resolved"

# ``![[equation:id]]`` / ``[[equation:id]]`` embeds in course snapshot text
# (same two forms ``core/lecture.py::_resolve_equation_embeds`` resolves).
_EQUATION_EMBED_RE = re.compile(r"!?\[\[\s*equation\s*:\s*([^\]]+)\]\]", re.IGNORECASE)


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
    """``document_figures`` rows keyed by normalized ``figure_key`` and by
    ``caption_block_id``.

    Figures are persisted in the ``figure_image_extraction`` stage (well
    before this one runs), so these rows — including the real DB ``id`` and
    ``inner_labels`` — are always available here, fresh run or resumed.

    The ``by_key`` map is keyed by ``normalize_figure_join_key(figure_key)``,
    not the raw ``figure_key`` string: ``figure_table_semantics``'s
    ``FigureRecord.figure_id`` keeps the caption label's punctuation
    (``fig_3.3``) while ``document_figures.figure_key`` is already
    alnum-normalized (``fig_3_3``) — a raw-string lookup would fail for every
    chapter-numbered label. See ``figure_images.normalize_figure_join_key``
    (the single source of truth for this normalization); callers must
    normalize the same way on lookup (``_resolve_figure_db_row`` does).
    """
    try:
        from .figure_images import load_document_figures, normalize_figure_join_key
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
        key = normalize_figure_join_key(row.get("figure_key"))
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
    """Resolve the ``document_figures`` row a ``FigureRecord`` corresponds to.

    Mirrors ``context_lens._matching_figure_record``'s priority order: try a
    normalized ``figure_key`` match first (``fig_dict["figure_id"]`` keeps
    the caption label's punctuation, e.g. ``fig_3.3``, while
    ``figure_rows_by_key`` is keyed by ``normalize_figure_join_key`` output,
    e.g. ``fig_3_3`` — both sides go through the same normalization; see
    ``figure_images.normalize_figure_join_key``), then fall back to
    ``caption_block_id`` (``source_location.caption_block_id``) when no
    normalized key match is found (e.g. legacy rows with an unresolvable
    figure_key).
    """
    from .figure_images import normalize_figure_join_key

    key = normalize_figure_join_key(fig_dict.get("figure_id"))
    row = figure_rows_by_key.get(key) if key else None
    if row is None:
        source_location = fig_dict.get("source_location") or {}
        caption_block_id = str(source_location.get("caption_block_id") or "")
        row = figure_rows_by_caption_block.get(caption_block_id) if caption_block_id else None
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
# Required equations (Phase 3 selection fix — 指示書 §5.2 /
# element_context_presentation_redesign.md §8 Phase 3: "教材本文に
# ``![[equation:id]]`` で埋め込まれた式は必ず対象").
#
# Equations used to sit last in a single ``component -> figure -> claim ->
# equation`` concatenation that was then sliced by ``max_elements``, so on any
# sizeable document *every* equation fell off the end and never got a
# contextual explanation — precisely the elements whose readable one-line
# headline Phase 3 exists to produce. Equations that the teaching material
# actually shows are now collected up front and reserved outside the cap.
# ---------------------------------------------------------------------------


def _normalize_equation_id(value: Any) -> str:
    """Fold an equation reference into the shared ``![[kind:id]]`` key space.

    The normalization rule itself lives in
    ``core.course_content_builder.normalize_evidence_id`` (the single source of
    truth shared with ``lsNormalizeEvidenceId`` / ``normalizeMaterialEvidenceId``
    on the frontend); this stage must not re-implement it (指示書 §5.2 "ID
    正規化規則を course builder と二重実装しない"). Imported lazily — the course
    builder pulls in the LLM / DB stack that this module has no other reason to
    load — and fail-soft to a plain trim so a broken import degrades matching
    rather than killing the stage.
    """
    try:
        from core.course_content_builder import normalize_evidence_id
    except Exception:  # noqa: BLE001
        logger.warning(
            "contextual_explanation: normalize_evidence_id unavailable; "
            "falling back to trim-only equation id matching (non-fatal)",
            exc_info=True,
        )
        return str(value or "").strip()
    return normalize_evidence_id(value)


def build_course_snapshot_equation_ids(material_id: str) -> list[str]:
    """Normalized equation ids embedded as ``![[equation:id]]`` in course text.

    The strongest possible evidence that a given equation is *presented to a
    learner* is that a course snapshot already embeds it — including embeds a
    teacher authored by hand, which no artifact-side link can reproduce. This
    is the only reason the stage looks at ``learning_courses`` at all; the
    lookup is bounded (:data:`MAX_COURSE_SNAPSHOTS`), read-only and fail-soft
    (any error yields ``[]``, i.e. the other required sources still apply).

    On a document's *first* analysis no course references it yet, so this
    returns nothing; the value shows up on re-analysis of an in-use document.
    """
    material_id = str(material_id or "").strip()
    if not material_id:
        return []
    try:
        from sqlalchemy import text as sa_text

        from core.course_data import iter_all_topics
        from core.lecture import topic_spoken_script, topic_student_material
        from core.postgres import get_session
    except Exception:  # noqa: BLE001
        return []

    try:
        session = get_session()
    except Exception:  # noqa: BLE001
        logger.warning(
            "contextual_explanation: course snapshot lookup unavailable for material=%s (non-fatal)",
            material_id, exc_info=True,
        )
        return []
    try:
        rows = session.execute(
            sa_text(
                """
                SELECT lc.data
                FROM learning_courses lc
                WHERE EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(COALESCE(lc.data->'sources', '[]'::jsonb)) AS src
                    WHERE src->>'material_id' = :material_id
                )
                LIMIT :limit
                """
            ),
            {"material_id": material_id, "limit": MAX_COURSE_SNAPSHOTS},
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning(
            "contextual_explanation: course snapshot lookup failed for material=%s (non-fatal)",
            material_id, exc_info=True,
        )
        return []
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass

    ordered: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        data = row[0] if not isinstance(row, dict) else row.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:  # noqa: BLE001
                continue
        if not isinstance(data, dict):
            continue
        for topic in iter_all_topics(data):
            for chunk in (topic_student_material(topic), topic_spoken_script(topic)):
                for match in _EQUATION_EMBED_RE.finditer(chunk or ""):
                    eq_id = _normalize_equation_id(match.group(1))
                    if eq_id and eq_id not in seen:
                        seen.add(eq_id)
                        ordered.append(eq_id)
    return ordered


def _component_equation_refs(component_result: Any) -> list[str]:
    """Equation ids the course builder's material rule reaches from components.

    Mirrors ``course_content_builder._equations_for_components``: component
    ``linked_equation_ids`` plus ``evidence_refs.equation_ids``. (The course
    builder additionally keeps only the first 5 *per topic*; that cap belongs
    to one topic's presentation, not to "is this equation ever shown", so it is
    deliberately not applied here.)
    """
    refs: list[str] = []
    for comp in getattr(component_result, "components", []) or []:
        refs.extend(getattr(comp, "linked_equation_ids", []) or [])
        evidence_refs = getattr(comp, "evidence_refs", None)
        if isinstance(evidence_refs, dict):
            refs.extend(evidence_refs.get("equation_ids") or [])
    return [str(ref) for ref in refs if ref]


def _thesis_equation_refs(thesis: Any) -> list[str]:
    """``central_thesis`` / ``support_structure[]`` の ``equation_ids``.

    Same flattening as ``persistence._thesis_ref_nodes`` (central node +
    every support entry), reading the one field that helper drops.
    """
    refs: list[str] = []
    central = getattr(thesis, "central_thesis", None)
    if isinstance(central, dict):
        refs.extend(central.get("equation_ids") or [])
    support_structure = getattr(thesis, "support_structure", None)
    if isinstance(support_structure, dict):
        for entries in support_structure.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    refs.extend(entry.get("equation_ids") or [])
    return [str(ref) for ref in refs if ref]


def _derivation_result_equation_refs(derivations: Any) -> list[str]:
    """Each derivation chain's **terminal output** equations.

    Deliberately narrow: a chain's steps touch nearly every equation in the
    document (each step's output is the next step's input), so treating the
    whole chain as "教材提示対象" would make the required set ≈ all equations and
    empty the reserved-quota design of meaning. What the material presents as a
    derivation is its *result*, so only the chain-level ``output_equation_ids``
    (system-level chains) — or, failing that, the last step's outputs — count.
    Inputs and intermediates still reach the required set whenever a component,
    claim, thesis node or course embed actually references them.
    """
    refs: list[str] = []
    for chain in getattr(derivations, "chains", []) or []:
        chain_outputs = [str(e) for e in (getattr(chain, "output_equation_ids", []) or []) if e]
        if chain_outputs:
            refs.extend(chain_outputs)
            continue
        steps = getattr(chain, "steps", []) or []
        for step in reversed(steps):
            step_outputs = [str(e) for e in (getattr(step, "output_equation_ids", []) or []) if e]
            if step_outputs:
                refs.extend(step_outputs)
                break
    return refs


def collect_required_equation_ids(
    *,
    component_result: Any,
    claim_objects: Any,
    thesis: Any,
    derivations: Any = None,
    course_equation_ids: Iterable[str] | None = None,
) -> list[str]:
    """Normalized ids of equations the teaching material presents, in priority order.

    Sources, strongest evidence first: course snapshot embeds, the component
    rule the course builder itself uses, claim ``equation_ids``, thesis nodes,
    and derivation results. Order-preserving and de-duplicated so one equation
    is only ever built once (指示書 §5.2 "同じ equation は1回だけ入力する").
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(raw: Any) -> None:
        eq_id = _normalize_equation_id(raw)
        if eq_id and eq_id not in seen:
            seen.add(eq_id)
            ordered.append(eq_id)

    for raw in course_equation_ids or []:
        _add(raw)
    for raw in _component_equation_refs(component_result):
        _add(raw)
    for claim in getattr(claim_objects, "claims", []) or []:
        for raw in getattr(claim, "equation_ids", []) or []:
            _add(raw)
    for raw in _thesis_equation_refs(thesis):
        _add(raw)
    for raw in _derivation_result_equation_refs(derivations):
        _add(raw)
    return ordered


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
    material_id: str | None = None,
    derivations: Any = None,
) -> tuple[list[ElementExplanationInput], dict]:
    """Build the (priority-ordered, capped) element list + a stage_outputs meta dict.

    Selection order (指示書 §5.2, superseding the flat concatenation of design
    §5.1 "選抜と上限"):

    1. **required equations** — equations the teaching material presents
       (:func:`collect_required_equation_ids`). Reserved *outside*
       ``max_elements``: they must not lose a priority contest to elements that
       merely happen to sort earlier.
    2. **priority elements** — component -> figure -> claim (thesis-anchored
       first), i.e. the existing order, unchanged.
    3. **optional equations** — every remaining equation, in artifact order.

    ``max_elements`` caps (2)+(3); anything beyond it is recorded as
    ``truncated`` in the returned meta, never silently lost from the count
    (P4). ``max_elements <= 0`` stays a hard operator kill switch ("select zero
    elements", matching apparatus_semantics's max-images-per-document
    convention) and disables the stage outright, reservation included.

    ``material_id`` (optional) enables the course-snapshot required source;
    ``derivations`` (optional) the derivation-result one. Both degrade to
    "source contributes nothing" when absent, so callers that cannot supply
    them still get the other required sources.
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

    def _equation_element(eq: Any) -> ElementExplanationInput:
        return build_equation_element(
            eq,
            claim_map=claim_map,
            equation_map=equation_map,
            thesis_ref_index=thesis_ref_index,
            resolve_library=resolve_library,
        )

    # Artifact equations indexed by *normalized* id, so a reference written as
    # ``eq_eq_F2`` in a course embed resolves to the artifact's ``eq_F2``
    # (first occurrence wins if two raw ids normalize to the same key).
    equation_by_normalized_id: dict[str, tuple[str, Any]] = {}
    for raw_eq_id, eq in equation_map.items():
        key = _normalize_equation_id(raw_eq_id)
        if key and key not in equation_by_normalized_id:
            equation_by_normalized_id[key] = (raw_eq_id, eq)

    # A course spans several documents, so most of its embeds legitimately name
    # equations from a *sibling* document. Those are not this document's
    # business: keep only the ones this artifact can resolve, rather than
    # reporting them as unresolved on every re-analysis.
    course_equation_ids = [
        eq_id
        for eq_id in (
            _normalize_equation_id(raw)
            for raw in build_course_snapshot_equation_ids(material_id or "")
        )
        if eq_id in equation_by_normalized_id
    ]
    required_equation_ids = collect_required_equation_ids(
        component_result=component_result,
        claim_objects=claim_objects,
        thesis=thesis,
        derivations=derivations,
        course_equation_ids=course_equation_ids,
    )

    required_equation_elements: list[ElementExplanationInput] = []
    required_raw_ids: set[str] = set()
    required_unresolved = 0
    for eq_id in required_equation_ids:
        entry = equation_by_normalized_id.get(eq_id)
        if entry is None:
            # Never fabricate an input for an id the artifact does not have
            # (指示書 §5.2); record it instead (P4).
            required_unresolved += 1
            skipped.append({
                "element_type": ELEMENT_TYPE_EQUATION,
                "element_id": eq_id,
                "reason": SKIP_REASON_EQUATION_NOT_RESOLVED,
            })
            continue
        raw_eq_id, eq = entry
        required_raw_ids.add(raw_eq_id)
        required_equation_elements.append(_equation_element(eq))

    optional_equation_elements = [
        _equation_element(eq)
        for eq in getattr(equations, "equations", []) or []
        if str(getattr(eq, "equation_id", "") or "")
        and str(getattr(eq, "equation_id", "")) not in required_raw_ids
    ]

    capped_pool = (
        component_elements + figure_elements + claim_elements + optional_equation_elements
    )
    combined = required_equation_elements + capped_pool
    # max_elements=0 means "select zero elements" (a hard operator override,
    # matching apparatus_semantics's max-images-per-document convention), not
    # "cap disabled" — so this must not special-case 0 as unlimited, and the
    # required reservation does not survive the stage being switched off.
    if max_elements <= 0:
        elements: list[ElementExplanationInput] = []
        required_selected = 0
    else:
        elements = required_equation_elements + capped_pool[:max_elements]
        required_selected = len(required_equation_elements)
    truncated = len(elements) < len(combined)

    meta = {
        "considered": len(combined),
        "selected": len(elements),
        "truncated": truncated,
        "truncated_count": max(0, len(combined) - len(elements)),
        "counts_by_kind": {
            "component": len(component_elements),
            "figure": len(figure_elements),
            "claim": len(claim_elements),
            "equation": len(required_equation_elements) + len(optional_equation_elements),
        },
        "skipped": skipped,
        # 指示書 §5.2: required 数式の選抜を stage artifact から追跡できるようにする
        # （既存キーの意味は変えない）。considered = 教材提示対象として集めた
        # 正規化済み ID 数、selected = 実際に入力化した数、unresolved = artifact に
        # 実体が無く ``equation_not_resolved`` として skipped に記録した数。
        "required_equations_considered": len(required_equation_ids),
        "required_equations_selected": required_selected,
        "required_equations_unresolved": required_unresolved,
    }
    return elements, meta
