"""ClaimQualificationAgent: quality gate for role-labeled claim candidates."""
from __future__ import annotations

import logging

from episteme_graph.agents.document_structure.schema import DocumentStructureResult
from episteme_graph.agents.paper_skeleton.schema import PaperSkeletonResult
from episteme_graph.agents.rhetorical_role.schema import RhetoricalRoleResult

from .cartridge_loader import CartridgeLoader
from .context_lint import unresolved_fragments
from .input_builder import ClaimQualificationInputBuilder
from .llm_client import ClaimQualificationLLMClient
from .prompt import ClaimQualificationPromptFactory
from .repair import ClaimQualificationRepairer, _fallback_record, _parse_record
from .schema import (
    CartridgeContext,
    ClaimQualificationResult,
    QualifiedSpanRecord,
)
from .validator import ClaimQualificationValidator

logger = logging.getLogger(__name__)


class ClaimQualificationAgent:
    """Evaluate role-labeled spans for claim adoption, tier, and granularity."""

    def __init__(
        self,
        cartridge_base_dir: str | None = None,
        llm_model: str | None = None,
    ) -> None:
        self._cartridge_loader = CartridgeLoader(cartridge_base_dir)
        self._input_builder = ClaimQualificationInputBuilder()
        self._prompt_factory = ClaimQualificationPromptFactory()
        self._llm_client = ClaimQualificationLLMClient(model=llm_model)
        self._validator = ClaimQualificationValidator()
        self._repairer = ClaimQualificationRepairer()

    def run(
        self,
        structure: DocumentStructureResult,
        skeleton: PaperSkeletonResult,
        roles: RhetoricalRoleResult,
        cartridge_id: str | None = None,
        config: dict | None = None,
        progress_callback=None,
    ) -> ClaimQualificationResult:
        cartridge = self._load_cartridge(cartridge_id)
        llm_inputs = self._input_builder.build(
            structure, skeleton, roles, cartridge=cartridge, config=config
        )
        if not llm_inputs:
            return ClaimQualificationResult.make_fallback(
                roles.document_id, cartridge_id, None, "No target spans for qualification"
            )

        records: list[QualifiedSpanRecord] = []
        total = len(llm_inputs)
        for idx, llm_input in enumerate(llm_inputs, start=1):
            messages = self._prompt_factory.build_messages(llm_input, cartridge)
            try:
                raw_output = self._llm_client.generate(messages)
            except Exception as exc:
                logger.exception(
                    "Claim qualification failed for document=%s span_id=%s block_id=%s cartridge=%s",
                    roles.document_id,
                    llm_input.span_id,
                    llm_input.block_id,
                    llm_input.cartridge_id,
                )
                records.append(_fallback_record(llm_input, str(exc)))
                if progress_callback:
                    progress_callback(idx, total)
                continue

            record = _parse_record(raw_output, llm_input)
            record, raw_output = self._retry_unresolved_references(
                record, raw_output, llm_input, cartridge
            )
            partial = self._build_result(
                document_id=roles.document_id,
                cartridge_id=llm_input.cartridge_id,
                records=[record],
            )
            issues = self._validator.validate(partial, cartridge)
            errors = [i for i in issues if i.severity == "error"]
            if errors:
                logger.warning(
                    "Claim qualification validation repair required for document=%s span_id=%s errors=%d",
                    roles.document_id,
                    llm_input.span_id,
                    len(errors),
                )
                record = self._repairer.repair(
                    llm_input=llm_input,
                    raw_output=raw_output,
                    validation_issues=issues,
                    cartridge=cartridge,
                    llm_client=self._llm_client,
                    prompt_factory=self._prompt_factory,
                    validator=self._validator,
                )
            records.append(record)
            if progress_callback:
                progress_callback(idx, total)

        result = self._build_result(
            document_id=roles.document_id,
            cartridge_id=cartridge.cartridge_id if cartridge else cartridge_id,
            records=records,
        )
        result.validation_issues = self._validator.validate(result, cartridge)
        return result

    def _retry_unresolved_references(
        self,
        record: QualifiedSpanRecord,
        raw_output: dict,
        llm_input,
        cartridge: CartridgeContext | None,
    ):
        """Single targeted retry when the context lint demoted claims (#357).

        The lint in _parse_record demoted accepted atomic claims with
        unresolved anaphora; ask the LLM once to name the concrete referent.
        If the retry still leaves unresolved references (or fails), keep the
        demoted record — review_required, never silently accepted.
        """
        fragments = unresolved_fragments(record.atomic_claims)
        if not fragments:
            return record, raw_output
        try:
            retry_messages = self._prompt_factory.build_reference_repair_messages(
                llm_input, raw_output, fragments, cartridge
            )
            retry_raw = self._llm_client.generate(retry_messages)
        except Exception as exc:
            logger.warning(
                "Unresolved-reference retry failed for span_id=%s: %s",
                llm_input.span_id, exc,
            )
            return record, raw_output
        retry_record = _parse_record(retry_raw, llm_input)
        if unresolved_fragments(retry_record.atomic_claims):
            return record, raw_output
        # Information must never be lost (#357): a retry that silently drops
        # atomic claims is not a fix — keep the demoted original instead.
        if len(retry_record.atomic_claims) < len(record.atomic_claims):
            logger.warning(
                "Unresolved-reference retry for span_id=%s dropped atomic claims "
                "(%d -> %d); keeping the demoted original",
                llm_input.span_id,
                len(record.atomic_claims),
                len(retry_record.atomic_claims),
            )
            return record, raw_output
        return retry_record, retry_raw

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

    @staticmethod
    def _build_result(
        document_id: str,
        cartridge_id: str | None,
        records: list[QualifiedSpanRecord],
    ) -> ClaimQualificationResult:
        accepted: list[QualifiedSpanRecord] = []
        rejected: list[dict] = []
        deferred: list[dict] = []
        split_suggested = 0
        merge_suggested = 0
        atomic_claims_total = 0

        for record in records:
            status = record.qualification.get("status")
            if status == "accepted":
                accepted.append(record)
            elif status == "rejected":
                rejected.append(record.__dict__)
            else:
                deferred.append(record.__dict__)

            if record.edit_suggestions.get("should_split"):
                split_suggested += 1
            if (
                record.edit_suggestions.get("should_merge_with_prev")
                or record.edit_suggestions.get("should_merge_with_next")
            ):
                merge_suggested += 1
            atomic_claims_total += len(getattr(record, "atomic_claims", None) or [])

        return ClaimQualificationResult(
            document_id=document_id,
            cartridge_id=cartridge_id,
            qualified_spans=accepted,
            rejected_spans=rejected,
            deferred_spans=deferred,
            summary_stats={
                "accepted": len(accepted),
                "rejected": len(rejected),
                "deferred": len(deferred),
                "split_suggested": split_suggested,
                "merge_suggested": merge_suggested,
                "atomic_claims": atomic_claims_total,
            },
        )
