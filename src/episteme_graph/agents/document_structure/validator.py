"""StructureValidator: DocumentStructureResult の自己検証を行い ValidationIssue を返す。

- section title に本文断片が混入していないか
- figure_caption が body_paragraph になっていないか
- equation_block が paragraph に混ざっていないか
- references セクション以降が本文として扱われていないか
- block_type が許可語彙にあるか
- unknown の比率が異常に高くないか
- cartridge があれば notation pattern との矛盾チェック
"""
from __future__ import annotations

import re

from .schema import (
    BLOCK_TYPES,
    CartridgeContext,
    DocumentStructureResult,
    TypedBlock,
    ValidationIssue,
)

_FIGURE_RE = re.compile(r"^(Figure|Fig\.?|FIG\.?|図)\s*\d+", re.IGNORECASE)
_TABLE_RE = re.compile(r"^(Table|TABLE|表)\s*\d+", re.IGNORECASE)
_REF_ENTRY_PREFIX_RE = re.compile(r"^\[\d+\]")

_MAX_SECTION_TITLE_LEN = 200
_MAX_UNKNOWN_RATIO = 0.50


class StructureValidator:
    def validate(
        self,
        result: DocumentStructureResult,
        cartridge: CartridgeContext | None = None,
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        blocks = result.blocks

        issues += self._check_block_types(blocks)
        issues += self._check_section_title_length(blocks)
        issues += self._check_figure_caption_misclassified(blocks)
        issues += self._check_equation_misclassified(blocks)
        issues += self._check_reference_as_body(blocks)
        issues += self._check_unknown_ratio(blocks)

        if cartridge:
            issues += self._check_notation_in_heading(blocks, cartridge)

        return issues

    # ------------------------------------------------------------------

    def _check_block_types(self, blocks: list[TypedBlock]) -> list[ValidationIssue]:
        return [
            ValidationIssue(
                rule_id="invalid_block_type",
                severity="error",
                message=f"Invalid block_type '{b.block_type}' in {b.block_id}",
                block_id=b.block_id,
            )
            for b in blocks
            if b.block_type not in BLOCK_TYPES
        ]

    def _check_section_title_length(
        self, blocks: list[TypedBlock]
    ) -> list[ValidationIssue]:
        issues = []
        for b in blocks:
            if b.block_type in ("section_heading", "subsection_heading"):
                if len(b.text) > _MAX_SECTION_TITLE_LEN:
                    issues.append(
                        ValidationIssue(
                            rule_id="section_title_too_long",
                            severity="warning",
                            message=(
                                f"Section title too long ({len(b.text)} chars): "
                                f"'{b.text[:60]}...'"
                            ),
                            block_id=b.block_id,
                        )
                    )
        return issues

    def _check_figure_caption_misclassified(
        self, blocks: list[TypedBlock]
    ) -> list[ValidationIssue]:
        issues = []
        for b in blocks:
            if b.block_type == "body_paragraph" and _FIGURE_RE.match(b.text):
                issues.append(
                    ValidationIssue(
                        rule_id="figure_caption_as_body",
                        severity="warning",
                        message=f"Possible figure caption in body_paragraph: '{b.text[:60]}'",
                        block_id=b.block_id,
                    )
                )
        return issues

    def _check_equation_misclassified(
        self, blocks: list[TypedBlock]
    ) -> list[ValidationIssue]:
        # Look for body_paragraph that is just an equation label "(3.14)"
        _EQ_ONLY_RE = re.compile(r"^\(\s*[A-Z]?\d+(?:\.\d+)*\s*\)$")
        issues = []
        for b in blocks:
            if b.block_type == "body_paragraph" and _EQ_ONLY_RE.match(b.text.strip()):
                issues.append(
                    ValidationIssue(
                        rule_id="equation_as_body",
                        severity="warning",
                        message=f"Possible equation label misclassified as body: '{b.text[:40]}'",
                        block_id=b.block_id,
                    )
                )
        return issues

    def _check_reference_as_body(
        self, blocks: list[TypedBlock]
    ) -> list[ValidationIssue]:
        issues = []
        for b in blocks:
            if b.block_type == "body_paragraph" and _REF_ENTRY_PREFIX_RE.match(b.text):
                issues.append(
                    ValidationIssue(
                        rule_id="reference_as_body",
                        severity="warning",
                        message=f"Possible reference entry as body_paragraph: '{b.text[:60]}'",
                        block_id=b.block_id,
                    )
                )
        return issues

    def _check_unknown_ratio(self, blocks: list[TypedBlock]) -> list[ValidationIssue]:
        if not blocks:
            return []
        n_unknown = sum(1 for b in blocks if b.block_type == "unknown")
        ratio = n_unknown / len(blocks)
        if ratio > _MAX_UNKNOWN_RATIO:
            return [
                ValidationIssue(
                    rule_id="high_unknown_ratio",
                    severity="warning",
                    message=(
                        f"High proportion of unknown blocks: "
                        f"{ratio:.0%} ({n_unknown}/{len(blocks)})"
                    ),
                    block_id=None,
                )
            ]
        return []

    def _check_notation_in_heading(
        self, blocks: list[TypedBlock], cartridge: CartridgeContext
    ) -> list[ValidationIssue]:
        """カートリッジの notation pattern が見出しに現れた場合に warning を出す。"""
        if not cartridge.notation_patterns:
            return []

        compiled = []
        for np in cartridge.notation_patterns:
            pattern = np.get("pattern") if isinstance(np, dict) else None
            if pattern:
                try:
                    compiled.append(re.compile(pattern))
                except re.error:
                    pass

        if not compiled:
            return []

        issues = []
        for b in blocks:
            if b.block_type not in ("section_heading", "subsection_heading"):
                continue
            for pat in compiled:
                if pat.search(b.text):
                    issues.append(
                        ValidationIssue(
                            rule_id="notation_in_heading",
                            severity="warning",
                            message=(
                                f"Cartridge notation pattern matched in heading "
                                f"'{b.text[:50]}' — possible misclassification"
                            ),
                            block_id=b.block_id,
                        )
                    )
                    break
        return issues
