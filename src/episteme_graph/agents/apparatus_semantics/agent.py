"""ApparatusSemanticsAgent: identify experimental apparatus/instruments in figures.

Design doc: docs/features/image_pipeline_knowledge_library_design.md §5.

LLM-first, vision-enabled, candidate-only (design principle #2 — "画像の意味
解釈は candidate 止まり、確定は人間"): the agent never asserts a confirmed
apparatus identification. Cartridge-aware but not cartridge-dependent (works
with ``cartridge_id=None``), and library-aware but not library-dependent
(``library_candidates`` empty/None degrades to novel/unknown identification —
design principle #5).
"""
from __future__ import annotations

import logging

from .cartridge_loader import CartridgeLoader
from .input_builder import ApparatusSemanticsInputBuilder
from .llm_client import ApparatusSemanticsLLMClient
from .prompt import ApparatusSemanticsPromptFactory
from .repair import ApparatusSemanticsRepairer, _fallback_record, _parse_record
from .schema import (
    ApparatusRecord,
    ApparatusSemanticsResult,
    CartridgeContext,
    FigureImageInput,
    LibraryCandidate,
)
from .validator import ApparatusSemanticsValidator

logger = logging.getLogger(__name__)

# Consider a label_ref a prefix match of an abbreviation key when at most this
# many trailing characters differ (e.g. "RFPDs" against key "RFPD" — plural
# 's' suffix). Not a similarity/fuzzy-match algorithm — purely a bounded
# suffix allowance so validator.py / this module agree on the same rule.
_ABBREVIATION_PREFIX_SLACK = 3


def _expand_label(label: str | None, abbreviations: dict[str, str] | None) -> str:
    """Deterministic abbreviation expansion for one label/part name.

    Match priority: exact key -> case-insensitive key -> "label starts with
    key, with at most a few trailing characters left over" (covers plural /
    minor suffixes such as "RFPDs" vs. "RFPD"). Never taken from the LLM —
    this is a plain dictionary lookup against ``FigureImageInput.abbreviations``.
    """
    if not label or not abbreviations:
        return ""
    label = label.strip()
    if not label:
        return ""
    if label in abbreviations:
        return abbreviations[label]

    label_ci = label.casefold()
    for key, value in abbreviations.items():
        if key.casefold() == label_ci:
            return value

    for key, value in abbreviations.items():
        key_ci = key.casefold()
        if not key_ci:
            continue
        if label_ci.startswith(key_ci) and len(label) - len(key) <= _ABBREVIATION_PREFIX_SLACK:
            return value

    return ""


def _attach_label_grounding(record: ApparatusRecord, figure: FigureImageInput) -> None:
    """Deterministically attach ``bbox`` / ``expanded_name`` to each part.

    ``label_ref`` is the only part of this grounding trusted from the LLM
    (validator.py already checked it against ``figure.inner_labels``). bbox is
    a spatial fact and expanded_name is a dictionary lookup — both are plain
    data lookups performed here, never taken verbatim from the LLM (design
    principle #2/#4). No-op when ``record.parts`` is empty (fallback records).
    """
    label_bbox: dict[str, list | None] = {}
    label_bbox_ci: dict[str, list | None] = {}
    for label in figure.inner_labels or []:
        if not isinstance(label, dict):
            continue
        text = str(label.get("text", "") or "").strip()
        if not text:
            continue
        bbox = label.get("bbox")
        label_bbox.setdefault(text, bbox)
        label_bbox_ci.setdefault(text.casefold(), bbox)

    for part in record.parts:
        label_ref = (part.label_ref or "").strip() or None
        bbox = None
        if label_ref:
            if label_ref in label_bbox:
                bbox = label_bbox[label_ref]
            elif label_ref.casefold() in label_bbox_ci:
                bbox = label_bbox_ci[label_ref.casefold()]
        part.bbox = list(bbox) if bbox else None
        part.expanded_name = _expand_label(label_ref or part.name, figure.abbreviations)


def _attach_profile_grounding(record: ApparatusRecord, figure: FigureImageInput) -> None:
    """Copy only deterministic PDF-label grounding into mode profile items."""
    profile = record.analysis_profile if isinstance(record.analysis_profile, dict) else {}
    label_bbox: dict[str, list | None] = {}
    for label in figure.inner_labels or []:
        if not isinstance(label, dict):
            continue
        label_text = str(label.get("text") or "").strip()
        if label_text:
            label_bbox.setdefault(label_text.casefold(), label.get("bbox"))

    parts_by_label = {
        str(part.label_ref or "").strip().casefold(): part
        for part in record.parts
        if str(part.label_ref or "").strip()
    }
    parts_by_name = {
        str(part.name or "").strip().casefold(): part
        for part in record.parts
        if str(part.name or "").strip()
    }
    for key in ("functions", "subjects", "regions", "observations", "highlights"):
        for item in profile.get(key) or []:
            if not isinstance(item, dict):
                continue
            label_ref = str(item.get("label_ref") or "").strip()
            name = str(item.get("name") or item.get("label") or "").strip()
            part = parts_by_label.get(label_ref.casefold()) or parts_by_name.get(name.casefold())
            # A model-provided bbox is not a PDF page-coordinate fact. Remove
            # it unless it can be replaced by deterministic text-layer data.
            item["bbox"] = None
            if part is not None:
                item["bbox"] = list(part.bbox) if part.bbox else None
                item["expanded_name"] = part.expanded_name
                if not item.get("evidence_quote"):
                    item["evidence_quote"] = part.evidence_quote
                continue
            bbox = label_bbox.get(label_ref.casefold()) if label_ref else None
            if bbox:
                item["bbox"] = list(bbox)
                item["expanded_name"] = _expand_label(label_ref, figure.abbreviations)


class ApparatusSemanticsAgent:
    def __init__(
        self,
        cartridge_id: str | None = None,
        llm_client: ApparatusSemanticsLLMClient | None = None,
        cartridge_loader: CartridgeLoader | None = None,
    ) -> None:
        self._default_cartridge_id = cartridge_id
        self._cartridge_loader = cartridge_loader or CartridgeLoader()
        self._input_builder = ApparatusSemanticsInputBuilder()
        self._prompt_factory = ApparatusSemanticsPromptFactory()
        self._llm_client = llm_client or ApparatusSemanticsLLMClient()
        self._validator = ApparatusSemanticsValidator()
        self._repairer = ApparatusSemanticsRepairer()

    def run(
        self,
        *,
        document_id: str,
        figures: list[FigureImageInput],
        library_candidates: dict[str, list[LibraryCandidate]] | None = None,
        cartridge_id: str | None = None,
    ) -> ApparatusSemanticsResult:
        resolved_cartridge_id = cartridge_id or self._default_cartridge_id
        cartridge = self._load_cartridge(resolved_cartridge_id)
        library_candidates = library_candidates or {}
        figures_by_id = {figure.figure_id: figure for figure in figures}
        candidate_ids_by_figure = {
            figure.figure_id: {
                candidate.entry_id
                for candidate in (library_candidates.get(figure.figure_id) or [])
            }
            for figure in figures
        }

        records = []
        for figure in figures:
            candidates = list(library_candidates.get(figure.figure_id) or [])

            # Image unavailable → never call the LLM; keep a reviewable
            # 'unknown' record instead of dropping the figure (P4).
            if not figure.image_bytes:
                fallback = _fallback_record(figure, "image_unavailable")
                _attach_label_grounding(fallback, figure)  # no-op: parts == []
                records.append(fallback)
                continue

            image_payload = self._input_builder.build_image_payload(figure)
            candidate_briefs = self._input_builder.build_candidate_briefs(candidates)
            nearby_text = self._input_builder.build_nearby_text(figure)
            cartridge_hints = self._input_builder.build_cartridge_hints(cartridge)
            inner_label_hints = self._input_builder.build_inner_label_hints(figure)
            abbreviations = self._input_builder.build_abbreviations(figure)
            messages = self._prompt_factory.build_messages(
                figure, candidate_briefs, nearby_text, cartridge_hints,
                inner_label_hints=inner_label_hints, abbreviations=abbreviations,
            )

            try:
                raw_output = self._llm_client.generate(messages, images=[image_payload])
            except Exception as exc:
                logger.exception(
                    "apparatus_semantics LLM call failed document=%s figure=%s",
                    document_id, figure.figure_id,
                )
                fallback = _fallback_record(figure, f"llm_call_failed: {exc}")
                _attach_label_grounding(fallback, figure)  # no-op: parts == []
                records.append(fallback)
                continue

            candidate_ids = {c.entry_id for c in candidates}
            record = _parse_record(raw_output, figure, candidates)
            issues = self._validator.validate_record(
                record, figure=figure, candidate_ids=candidate_ids,
            )
            if [i for i in issues if i.severity == "error"]:
                logger.warning(
                    "apparatus_semantics validation repair document=%s figure=%s errors=%d",
                    document_id, figure.figure_id,
                    len([i for i in issues if i.severity == "error"]),
                )
                record = self._repairer.repair(
                    figure=figure,
                    candidates=candidates,
                    candidate_briefs=candidate_briefs,
                    nearby_text=nearby_text,
                    cartridge_hints=cartridge_hints,
                    raw_output=raw_output,
                    validation_issues=issues,
                    llm_client=self._llm_client,
                    prompt_factory=self._prompt_factory,
                    validator=self._validator,
                    image_payload=image_payload,
                    inner_label_hints=inner_label_hints,
                    abbreviations=abbreviations,
                )
            # Deterministic bbox/expanded_name grounding — always applied last,
            # right before the record is kept, across every code path
            # (normal parse, repair success, repair exhaustion) (design
            # principle #2/#4: never taken from the LLM).
            _attach_label_grounding(record, figure)
            _attach_profile_grounding(record, figure)
            records.append(record)

        result = ApparatusSemanticsResult(
            document_id=document_id,
            cartridge_id=cartridge.cartridge_id if cartridge else resolved_cartridge_id,
            apparatus_records=records,
        )
        result.validation_issues = self._validator.validate(
            result,
            figures_by_id=figures_by_id,
            candidate_ids_by_figure=candidate_ids_by_figure,
        )
        return result

    def _load_cartridge(self, cartridge_id: str | None) -> CartridgeContext | None:
        if not cartridge_id:
            return None
        try:
            return self._cartridge_loader.load(cartridge_id)
        except FileNotFoundError:
            logger.warning(
                "Cartridge '%s' not found; proceeding without cartridge (apparatus_semantics)",
                cartridge_id,
            )
            return None
