"""Deterministic equation label/id normalization."""
from __future__ import annotations

import re

from episteme_graph.agents.document_structure.schema import TypedBlock

from .schema import NormalizedEquation

_TRAILING_LABEL_RE = re.compile(r"\((?P<label>[A-Za-z]?\d+(?:\.\d+)*|[A-Za-z]\.\d+)\)\s*$")

# Issue #368: source-aware equation label validity.
#
# A PDF parser / GROBID can emit garbage labels such as "(", "(1) g , K (3)",
# "1 3 ." or "(1) g and K". These must not be promoted into equation ids. The
# allowed patterns below describe legitimate numeric / appendix equation
# numbers; cartridges may extend them. Labels that match none of the patterns
# are rejected (normalized to None). Note: a *missing* label is valid (an
# unnumbered display equation), and trusted TeX `\label{...}` semantic keys are
# accepted without being held to the numeric PDF patterns.
DEFAULT_ALLOWED_LABEL_PATTERNS = [
    r"^[A-Za-z]?\d+(?:\.\d+)*[A-Za-z]?$",   # 1, 12, 2.3.4, A1, 3a, A.2 -> "A2"? handled below
    r"^[A-Za-z]\.\d+(?:\.\d+)*$",            # A.2, B.10, C.3.1
    r"^[A-Za-z]+\d+(?:\.\d+)*$",             # eq12, S3 (semantic-ish numeric)
]


class EquationNormalizer:
    def __init__(self, extra_label_patterns: list[str] | None = None) -> None:
        self._allowed = [re.compile(p) for p in DEFAULT_ALLOWED_LABEL_PATTERNS]
        self.set_extra_label_patterns(extra_label_patterns)

    def set_extra_label_patterns(self, patterns: list[str] | None) -> None:
        """Cartridge-provided additional allowed label patterns (issue #368)."""
        self._extra: list[re.Pattern] = []
        for pattern in patterns or []:
            try:
                self._extra.append(re.compile(pattern))
            except re.error:
                continue

    def normalize(self, equation_block: TypedBlock) -> NormalizedEquation:
        text = equation_block.text.strip()
        raw_label = equation_block.equation_label or self.extract_label(text)
        source_trusted = self._is_trusted_source(equation_block)
        label, label_is_valid, rejected_label = self.validate_label(
            raw_label, source_trusted=source_trusted
        )
        equation_id = self.equation_id_from_label(label, equation_block.block_id)
        return NormalizedEquation(
            equation_id=equation_id,
            block_id=equation_block.block_id,
            section_id=equation_block.section_id,
            label=label,
            text=text,
            latex=equation_block.raw.get("latex") if isinstance(equation_block.raw, dict) else None,
            plain_text=self._plain_text(text),
            label_is_valid=label_is_valid,
            rejected_label=rejected_label,
        )

    @staticmethod
    def extract_label(text: str) -> str | None:
        match = _TRAILING_LABEL_RE.search(text.strip())
        return match.group("label") if match else None

    def validate_label(
        self,
        label: str | None,
        *,
        source_trusted: bool = False,
    ) -> tuple[str | None, bool, str | None]:
        """Source-aware label validation (issue #368).

        Returns ``(normalized_label, is_valid, rejected_label)``.

        - A missing label is valid (legitimate unnumbered equation).
        - Trusted TeX ``\\label{...}`` semantic keys are accepted as-is.
        - PDF/GROBID labels must match an allowed numeric/appendix pattern;
          otherwise they are rejected (normalized to None) and the original is
          returned as ``rejected_label`` for audit.
        """
        if label is None:
            return None, True, None
        stripped = str(label).strip()
        if not stripped:
            # An empty / whitespace label is rejected (must not yield "eq_").
            return None, False, str(label)
        # A single clean surrounding parenthesis pair is part of the printed
        # equation number, e.g. "(3.14)"; strip it before pattern matching.
        inner = stripped
        if inner.startswith("(") and inner.endswith(")") and inner.count("(") == 1 and inner.count(")") == 1:
            inner = inner[1:-1].strip()
        if not inner:
            return None, False, stripped
        if source_trusted:
            # Trusted TeX semantic labels (e.g. \label{eq:dispersion}) are kept;
            # only reject obviously broken values (whitespace runs / stray punct).
            if self._is_clean_semantic_label(inner):
                return inner, True, None
            return None, False, stripped
        if self._matches_allowed(inner):
            return inner, True, None
        return None, False, stripped

    def _matches_allowed(self, label: str) -> bool:
        return any(p.match(label) for p in self._allowed) or any(
            p.match(label) for p in self._extra
        )

    @staticmethod
    def _is_clean_semantic_label(label: str) -> bool:
        # Single token (optionally with ':' or '-' or '.'), no spaces / commas /
        # multiple parenthesised groups.
        if any(ch in label for ch in (",", "(", ")")):
            return False
        if " " in label.strip():
            return False
        return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9:_.\-]*$", label))

    @staticmethod
    def _is_trusted_source(block: TypedBlock) -> bool:
        raw = block.raw if isinstance(block.raw, dict) else {}
        if str(raw.get("extraction_source") or "") == "tex_source":
            return True
        return raw.get("parser_source") == "tex_archive"

    @staticmethod
    def _with_eq_prefix(normalized: str) -> str:
        # Idempotent ``eq_`` prefix. A label that already encodes "eq" — e.g.
        # ``eq:F2`` normalises to ``eq_F2`` — must NOT become ``eq_eq_F2``
        # (otherwise downstream references render as "未解決の数式"). Only prepend
        # when the normalised string does not already start with an eq marker.
        low = normalized.lower()
        if low == "eq" or low.startswith("eq_"):
            return normalized
        return f"eq_{normalized}"

    @staticmethod
    def equation_id_from_label(label: str | None, block_id: str) -> str:
        if label:
            normalized = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
            if normalized:
                return EquationNormalizer._with_eq_prefix(normalized)
        return f"eq_{block_id}"

    @staticmethod
    def ref_to_equation_id(ref: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]+", "_", ref).strip("_")
        return EquationNormalizer._with_eq_prefix(normalized) if normalized else "eq_"

    @staticmethod
    def _plain_text(text: str) -> str:
        return (
            text.replace("Λ", "Lambda")
            .replace("Γ", "Gamma")
            .replace("τ", "tau")
            .replace("ν", "nu")
        )
