"""EquationSemanticsAgent: recover local semantic roles for equation blocks.

Issue #245: candidates → EquationAcceptanceGate → LLM semantics の3段パイプライン。
"""
from __future__ import annotations

import logging

from episteme_graph.agents.document_structure.schema import DocumentStructureResult
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult

from .acceptance_gate import EquationAcceptanceGate
from .cartridge_loader import CartridgeLoader
from .image_extractor import EquationImageExtractor
from .input_builder import EquationSemanticsInputBuilder
from .llm_client import EquationSemanticsLLMClient
from .prompt import EquationSemanticsPromptFactory
from .reconstruction_layer import EquationReconstructionLayer
from .repair import EquationSemanticsRepairer, _fallback_record, _parse_record
from .schema import (
    CartridgeContext,
    EquationCandidate,
    EquationConsistency,
    EquationConfidencePolicy,
    EquationRecord,
    EquationReconstruction,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
)
from .validator import EquationSemanticsValidator

logger = logging.getLogger(__name__)


def _make_provisional_record(candidate: EquationCandidate) -> EquationRecord:
    """Create a minimal reviewable EquationRecord for a provisional candidate (issue #259).

    confidence_policy.can_support_claim is always False for provisional records.
    """
    src_loc = dict(candidate.source_location) if candidate.source_location else {}
    source_extraction = EquationSourceExtraction(
        raw_text=candidate.raw_text,
        latex=None,
        plain_text=candidate.raw_text or None,
        source_location=src_loc,
        extraction_source=str(src_loc.get("extraction_source") or "pdf_text_layer"),
        extraction_status=candidate.extraction_status,
        needs_math_review=True,
        review_reason=list(candidate.review_reason),
    )
    reconstruction = EquationReconstruction.make_none()
    semantics = EquationSemantics(
        equation_type="unknown",
        secondary_types=[],
        semantic_status="unknown",
        confidence=0.0,
        reason="provisional_candidate_no_llm_analysis",
        defined_symbols=[],
        used_symbols=[],
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary="Reviewable provisional equation candidate; source extraction is incomplete and requires math review.",
        review_flags=["needs_reconstruction", "partial_extraction"],
    )
    equation_consistency = EquationConsistency.derive(
        source_extraction=source_extraction,
        reconstruction=reconstruction,
        label=candidate.matched_label,
        semantics=semantics,
    )
    confidence_policy = EquationConfidencePolicy(
        can_support_claim=False,
        can_be_used_in_derivation=False,
        can_be_rendered_as_final_formula=False,
        allowed_downstream_use="semantic_hint_only",
        can_be_displayed_in_course=True,
        display_requires_note=True,
        must_not_treat_as_source_extracted=True,
    )
    eq_id = f"eq_provisional_{candidate.candidate_id}"
    candidate.accepted_equation_id = eq_id
    return EquationRecord(
        equation_id=eq_id,
        document_id=candidate.document_id,
        label=candidate.matched_label,
        candidate_trace_ids=[candidate.candidate_id],
        source_extraction=source_extraction,
        reconstruction=reconstruction,
        semantics=semantics,
        confidence_policy=confidence_policy,
        equation_consistency=equation_consistency,
    )


class EquationSemanticsAgent:
    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = EquationSemanticsInputBuilder()
        self._acceptance_gate = EquationAcceptanceGate()
        self._reconstruction_layer = EquationReconstructionLayer()
        self._image_extractor = EquationImageExtractor()
        self._prompt_factory = EquationSemanticsPromptFactory()
        self._llm_client = EquationSemanticsLLMClient(model=llm_model)
        self._validator = EquationSemanticsValidator()
        self._repairer = EquationSemanticsRepairer()

    def run(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult | None = None,
        roles: RhetoricalRoleResult | None = None,
        cartridge_id: str | None = None,
        config: dict | None = None,
        progress_callback=None,
    ) -> EquationSemanticsResult:
        cartridge = self._load_cartridge(cartridge_id)

        # Step 1: 候補生成 (全 equation_block から EquationCandidate を作る)
        candidates = self._input_builder.build_candidates(
            structure, skeleton=skeleton, roles=roles, cartridge=cartridge, config=config
        )
        if not candidates:
            return EquationSemanticsResult.make_fallback(
                structure.document_id, cartridge_id, None, "No equation blocks for semantics"
            )

        # Step 2: Acceptance gate (accepted / rejected / needs_merge / context_only / provisional を分類)
        classified_candidates = self._acceptance_gate.process(candidates)
        classified_candidates = self._reconstruction_layer.prepare_candidates(classified_candidates)
        reconstructable = self._reconstruction_layer.reconstructable_candidates(classified_candidates)

        if not reconstructable:
            logger.info(
                "document=%s: all %d equation candidates were rejected/context-only by acceptance gate",
                structure.document_id,
                len(classified_candidates),
            )
            result = EquationSemanticsResult(
                document_id=structure.document_id,
                cartridge_id=cartridge.cartridge_id if cartridge else cartridge_id,
                equation_candidates=classified_candidates,
                equations=[],
            )
            result.validation_issues = self._validator.validate(result, cartridge)
            return result

        # Step 3: LLM 入力構築
        # PDF 数式テキストは inline/display を問わず信用しない。accepted/provisional
        # すべてを復元対象にし、source text は監査用にだけ保持する。
        llm_inputs = self._input_builder.build_llm_inputs(
            structure,
            reconstructable,
            skeleton=skeleton,
            roles=roles,
            cartridge=cartridge,
            accepted_statuses={"accepted", "provisional"},
            force_reconstruction=True,
        )

        # Step 4: LLM semantics analysis
        candidate_by_id = {c.candidate_id: c for c in reconstructable}
        records: list[EquationRecord] = []
        total = len(llm_inputs)

        for idx, llm_input in enumerate(llm_inputs, start=1):
            candidate = candidate_by_id.get(llm_input.candidate_id)
            image = None if llm_input.source_is_trusted else self._image_extractor.crop_candidate(
                getattr(structure, "source_file", None),
                candidate.source_location if candidate else None,
            )
            messages = self._prompt_factory.build_messages(llm_input, cartridge)
            if image:
                messages[-1]["content"] += (
                    "\n\n## Equation Image\n"
                    "A cropped image of the equation candidate is attached. "
                    "Use the image as the primary source for LaTeX reconstruction; "
                    "use surrounding text only for disambiguation."
                )
            try:
                raw_output = self._llm_client.generate(
                    messages,
                    image=image.__dict__ if image else None,
                )
                if not image and llm_input.needs_reconstruction:
                    raw_output["_vision_ocr"] = {
                        "attempted": False,
                        "used": False,
                        "provider": None,
                        "model": None,
                        "reason": "equation_image_unavailable",
                    }
            except Exception as exc:
                logger.exception(
                    "Equation semantics failed for document=%s block_id=%s",
                    structure.document_id,
                    llm_input.block_id,
                )
                fallback = _fallback_record(llm_input, candidate, str(exc))
                _attach_source_image(fallback, image)
                records.append(fallback)
                if progress_callback:
                    progress_callback(idx, total)
                continue

            record = _parse_record(raw_output, llm_input, candidate)
            _attach_source_image(record, image)
            partial = EquationSemanticsResult(
                document_id=structure.document_id,
                cartridge_id=llm_input.cartridge_id,
                equation_candidates=[],
                equations=[record],
            )
            issues = self._validator.validate(partial, cartridge)
            if [i for i in issues if i.severity == "error"]:
                logger.warning(
                    "Equation semantics validation repair for document=%s block_id=%s errors=%d",
                    structure.document_id,
                    llm_input.block_id,
                    len([i for i in issues if i.severity == "error"]),
                )
                record = self._repairer.repair(
                    llm_input=llm_input,
                    candidate=candidate,
                    raw_output=raw_output,
                    validation_issues=issues,
                    cartridge=cartridge,
                    llm_client=self._llm_client,
                    prompt_factory=self._prompt_factory,
                    validator=self._validator,
                    image=image.__dict__ if image else None,
                )
                _attach_source_image(record, image)
            # accepted_equation_id を candidate に書き込む
            if candidate:
                candidate.accepted_equation_id = record.equation_id
            records.append(record)
            if progress_callback:
                progress_callback(idx, total)

        result = EquationSemanticsResult(
            document_id=structure.document_id,
            cartridge_id=cartridge.cartridge_id if cartridge else cartridge_id,
            equation_candidates=classified_candidates,
            equations=records,
        )
        result.validation_issues = self._validator.validate(result, cartridge)
        return result

    def _load_cartridge(self, cartridge_id: str | None) -> CartridgeContext | None:
        if not cartridge_id:
            return None
        try:
            return self._cartridge_loader.load(cartridge_id)
        except FileNotFoundError:
            logger.warning(
                "Cartridge '%s' not found; proceeding without cartridge", cartridge_id
            )
            return None


def _attach_source_image(record: EquationRecord, image: object | None) -> None:
    if not image:
        return
    try:
        record.source_extraction.source_image = {
            "mime_type": getattr(image, "mime_type"),
            "data_base64": getattr(image, "data_base64"),
            "page": getattr(image, "page"),
            "bbox": list(getattr(image, "bbox")),
            "source": "equation_semantics_bbox_crop",
        }
    except Exception:
        logger.warning("Failed to attach equation source image for %s", record.equation_id, exc_info=True)
