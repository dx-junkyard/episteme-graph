"""Validation for ThesisReconstructionAgent outputs."""
from __future__ import annotations

import re

from .schema import (
    GRAPH_RELATIONS,
    SUPPORT_SECTIONS,
    SUPPORT_TYPES,
    CartridgeContext,
    ThesisReconstructionResult,
    ValidationIssue,
)

_META_RE = re.compile(r"^(this paper|this section|figure|fig\.|table)\b", re.IGNORECASE)


class ThesisReconstructionValidator:
    def validate(
        self,
        result: ThesisReconstructionResult,
        cartridge: CartridgeContext | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        issues += self._check_central_thesis(result)
        issues += self._check_support_structure(result)
        issues += self._check_graph_hints(result)
        issues += self._check_confidence(result)
        issues += self._check_exclusions(result)
        issues += self._check_cartridge_terms(result, cartridge)
        return issues

    def _check_central_thesis(self, result: ThesisReconstructionResult) -> list[ValidationIssue]:
        thesis = result.central_thesis or {}
        text = thesis.get("text", "")
        issues = []
        if not text or not str(text).strip():
            issues.append(ValidationIssue(
                "empty_central_thesis",
                "error",
                "central_thesis.text is empty",
                "central_thesis.text",
            ))
        elif _META_RE.match(str(text).strip()):
            issues.append(ValidationIssue(
                "central_thesis_is_meta",
                "warning",
                "central_thesis appears to be meta/figure narration",
                "central_thesis.text",
            ))
        if not thesis.get("claim_ids") and not thesis.get("equation_ids") and not thesis.get("evidence_block_ids"):
            issues.append(ValidationIssue(
                "central_thesis_missing_evidence",
                "warning",
                "central_thesis has no claim/equation/evidence ids",
                "central_thesis",
            ))
        return issues

    def _check_support_structure(self, result: ThesisReconstructionResult) -> list[ValidationIssue]:
        support = result.support_structure or {}
        issues = []
        direct = support.get("direct_supports", [])
        if not direct:
            issues.append(ValidationIssue(
                "missing_direct_supports",
                "error",
                "support_structure.direct_supports is empty",
                "support_structure.direct_supports",
            ))
        for section in SUPPORT_SECTIONS:
            entries = support.get(section, [])
            if not isinstance(entries, list):
                issues.append(ValidationIssue(
                    "invalid_support_section",
                    "error",
                    f"support_structure.{section} must be a list",
                    f"support_structure.{section}",
                ))
                continue
            for idx, entry in enumerate(entries):
                support_type = entry.get("support_type")
                if section == "direct_supports" and support_type not in SUPPORT_TYPES:
                    issues.append(ValidationIssue(
                        "invalid_support_type",
                        "error",
                        f"direct support has invalid support_type={support_type!r}",
                        f"support_structure.direct_supports[{idx}].support_type",
                    ))
                if not entry.get("claim_ids") and not entry.get("equation_ids"):
                    issues.append(ValidationIssue(
                        "support_missing_evidence_ids",
                        "warning",
                        f"{section}[{idx}] has no claim_ids or equation_ids",
                        f"support_structure.{section}[{idx}]",
                    ))
                conf = entry.get("confidence", 0.0)
                if not self._in_range(conf):
                    issues.append(ValidationIssue(
                        "confidence_out_of_range",
                        "error",
                        f"{section}[{idx}] confidence out of range",
                        f"support_structure.{section}[{idx}].confidence",
                    ))
        return issues

    def _check_graph_hints(self, result: ThesisReconstructionResult) -> list[ValidationIssue]:
        issues = []
        for idx, hint in enumerate(result.thesis_graph_hints or []):
            relation = hint.get("relation")
            if relation not in GRAPH_RELATIONS:
                issues.append(ValidationIssue(
                    "invalid_graph_relation",
                    "error",
                    f"thesis_graph_hints[{idx}] has invalid relation={relation!r}",
                    f"thesis_graph_hints[{idx}].relation",
                ))
        return issues

    def _check_confidence(self, result: ThesisReconstructionResult) -> list[ValidationIssue]:
        issues = []
        if not self._in_range(result.confidence):
            issues.append(ValidationIssue(
                "confidence_out_of_range",
                "error",
                "result confidence is out of range",
                "confidence",
            ))
        thesis_conf = (result.central_thesis or {}).get("confidence", 0.0)
        if not self._in_range(thesis_conf):
            issues.append(ValidationIssue(
                "confidence_out_of_range",
                "error",
                "central_thesis confidence is out of range",
                "central_thesis.confidence",
            ))
        for idx, alt in enumerate(result.alternative_theses or []):
            if not self._in_range(alt.get("confidence", 0.0)):
                issues.append(ValidationIssue(
                    "confidence_out_of_range",
                    "error",
                    f"alternative_theses[{idx}] confidence out of range",
                    f"alternative_theses[{idx}].confidence",
                ))
        return issues

    def _check_exclusions(self, result: ThesisReconstructionResult) -> list[ValidationIssue]:
        if not result.excluded_from_core:
            return [ValidationIssue(
                "no_excluded_from_core",
                "warning",
                "excluded_from_core is empty; check whether prior/meta regions were separated",
                "excluded_from_core",
            )]
        return []

    def _check_cartridge_terms(
        self,
        result: ThesisReconstructionResult,
        cartridge: CartridgeContext | None,
    ) -> list[ValidationIssue]:
        if not cartridge or not cartridge.aliases:
            return []
        text = str((result.central_thesis or {}).get("text", "")).lower()
        matched = [
            canonical
            for canonical, aliases in cartridge.aliases.items()
            if canonical.lower() in text
            or any(str(alias).lower() in text for alias in aliases)
        ]
        if cartridge.aliases and not matched:
            return [ValidationIssue(
                "cartridge_terms_not_reflected",
                "warning",
                "central_thesis does not include any known cartridge canonical/alias terms",
                "central_thesis.text",
            )]
        return []

    @staticmethod
    def _in_range(value: object) -> bool:
        try:
            val = float(value)
        except (TypeError, ValueError):
            return False
        return 0.0 <= val <= 1.0
