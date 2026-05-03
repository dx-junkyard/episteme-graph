"""Repair helpers for EquationSemanticsAgent."""
from __future__ import annotations

import logging

from .llm_client import EquationSemanticsLLMClient
from .prompt import EquationSemanticsPromptFactory
from .schema import (
    CartridgeContext,
    DefinedSymbol,
    DerivationLinks,
    EquationLLMInput,
    EquationRolePrediction,
    EquationSemanticsRecord,
    EquationSemanticsResult,
    LocalAssumption,
    ValidationIssue,
)

logger = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = 2


class EquationSemanticsRepairer:
    def repair(
        self,
        llm_input: EquationLLMInput,
        raw_output: dict,
        validation_issues: list[ValidationIssue],
        cartridge: CartridgeContext | None,
        llm_client: EquationSemanticsLLMClient,
        prompt_factory: EquationSemanticsPromptFactory,
        validator: object,
    ) -> EquationSemanticsRecord:
        for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
            logger.info("Equation semantics repair attempt %d/%d", attempt, _MAX_REPAIR_ATTEMPTS)
            messages = prompt_factory.build_repair_messages(
                llm_input, raw_output, validation_issues, cartridge
            )
            try:
                raw_output = llm_client.generate(messages)
            except Exception as exc:
                logger.warning("Repair LLM call failed: %s", exc)
                break

            record = _parse_record(raw_output, llm_input)
            partial = _single_result(llm_input, record)
            remaining = validator.validate(partial, cartridge)  # type: ignore[attr-defined]
            if not [i for i in remaining if i.severity == "error"]:
                return record
            validation_issues = remaining

        return _fallback_record(llm_input, "Repair failed after max attempts")


def _parse_record(raw: dict, llm_input: EquationLLMInput) -> EquationSemanticsRecord:
    role_raw = raw.get("equation_role", {})
    if not isinstance(role_raw, dict):
        role_raw = {}
    confidence = role_raw.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    equation_role = EquationRolePrediction(
        primary=role_raw.get("primary", "unknown"),
        secondary=list(role_raw.get("secondary", [])),
        confidence=max(0.0, min(1.0, confidence)),
        reason=str(role_raw.get("reason", "")),
    )

    symbols = []
    for raw_symbol in raw.get("defined_symbols", []):
        if not isinstance(raw_symbol, dict):
            continue
        symbols.append(DefinedSymbol(
            symbol=str(raw_symbol.get("symbol", "")),
            definition_status=raw_symbol.get("definition_status", "unknown"),
            evidence_text=raw_symbol.get("evidence_text"),
        ))

    assumptions = []
    for raw_assumption in raw.get("local_assumptions", []):
        if not isinstance(raw_assumption, dict):
            continue
        assumptions.append(LocalAssumption(
            text=str(raw_assumption.get("text", "")),
            source_block_ids=list(raw_assumption.get("source_block_ids", [])),
        ))

    links_raw = raw.get("derivation_links", {})
    if not isinstance(links_raw, dict):
        links_raw = {}
    links = DerivationLinks(
        from_equations=list(links_raw.get("from_equations", [])),
        to_equations=list(links_raw.get("to_equations", [])),
        linked_text_spans=list(links_raw.get("linked_text_spans", [])),
    )

    return EquationSemanticsRecord(
        equation_id=raw.get("equation_id", llm_input.equation_id),
        block_id=raw.get("block_id", llm_input.block_id),
        section_id=raw.get("section_id", llm_input.section_id),
        section_title=raw.get("section_title", llm_input.section_title),
        label=raw.get("label", llm_input.label),
        text=raw.get("text", llm_input.equation_text),
        latex=raw.get("latex", llm_input.latex),
        plain_text=raw.get("plain_text", llm_input.plain_text),
        equation_role=equation_role,
        defined_symbols=symbols,
        local_assumptions=assumptions,
        derivation_links=links,
        summary=str(raw.get("summary", "")),
        review_flags=list(raw.get("review_flags", [])),
    )


def _fallback_record(llm_input: EquationLLMInput, reason: str) -> EquationSemanticsRecord:
    flags = ["ambiguous_role", "low_confidence"]
    if not llm_input.label:
        flags.append("missing_label")
    if not llm_input.prev_texts and not llm_input.next_texts:
        flags.append("broken_context")
    return EquationSemanticsRecord(
        equation_id=llm_input.equation_id,
        block_id=llm_input.block_id,
        section_id=llm_input.section_id,
        section_title=llm_input.section_title,
        label=llm_input.label,
        text=llm_input.equation_text,
        latex=llm_input.latex,
        plain_text=llm_input.plain_text,
        equation_role=EquationRolePrediction(
            primary="unknown",
            secondary=[],
            confidence=0.0,
            reason=reason,
        ),
        defined_symbols=[],
        local_assumptions=[],
        derivation_links=DerivationLinks([], [], []),
        summary="Equation semantics could not be inferred.",
        review_flags=flags,
    )


def _single_result(
    llm_input: EquationLLMInput,
    record: EquationSemanticsRecord,
) -> EquationSemanticsResult:
    return EquationSemanticsResult(
        document_id=llm_input.document_id,
        cartridge_id=llm_input.cartridge_id,
        equations=[record],
    )
