"""Deterministic equation label/id normalization."""
from __future__ import annotations

import re

from episteme_graph.agents.document_structure.schema import TypedBlock

from .schema import NormalizedEquation

_TRAILING_LABEL_RE = re.compile(r"\((?P<label>[A-Za-z]?\d+(?:\.\d+)*|[A-Za-z]\.\d+)\)\s*$")


class EquationNormalizer:
    def normalize(self, equation_block: TypedBlock) -> NormalizedEquation:
        text = equation_block.text.strip()
        label = equation_block.equation_label or self.extract_label(text)
        equation_id = self.equation_id_from_label(label, equation_block.block_id)
        return NormalizedEquation(
            equation_id=equation_id,
            block_id=equation_block.block_id,
            section_id=equation_block.section_id,
            label=label,
            text=text,
            latex=equation_block.raw.get("latex") if isinstance(equation_block.raw, dict) else None,
            plain_text=self._plain_text(text),
        )

    @staticmethod
    def extract_label(text: str) -> str | None:
        match = _TRAILING_LABEL_RE.search(text.strip())
        return match.group("label") if match else None

    @staticmethod
    def equation_id_from_label(label: str | None, block_id: str) -> str:
        if label:
            normalized = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_")
            return f"eq_{normalized}"
        return f"eq_{block_id}"

    @staticmethod
    def ref_to_equation_id(ref: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]+", "_", ref).strip("_")
        return f"eq_{normalized}"

    @staticmethod
    def _plain_text(text: str) -> str:
        return (
            text.replace("Λ", "Lambda")
            .replace("Γ", "Gamma")
            .replace("τ", "tau")
            .replace("ν", "nu")
        )
