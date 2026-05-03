"""Prompt construction for EquationSemanticsAgent."""
from __future__ import annotations

import json

from .schema import (
    DEFINITION_STATUSES,
    EQUATION_ROLES,
    LINKED_TEXT_RELATIONS,
    REVIEW_FLAGS,
    CartridgeContext,
    EquationLLMInput,
)

_SYSTEM_CONTENT = """\
You are analyzing a mathematical/display equation from a scientific paper.

Your task is NOT to prove the equation.
Your task is to reconstruct the equation's local semantic role in the paper.

Focus on:
1. whether the equation is a definition, relation, transformation, approximation, result, or constraint
2. which symbols are newly defined versus merely used
3. which local assumptions or limits the equation depends on
4. whether the equation is derived from nearby equations or explanatory text
5. producing a short reusable semantic summary
6. preferring cartridge-normalized terminology when available

Be conservative. If unsure whether a symbol is newly defined, mark it as unknown.
Return ONLY valid JSON matching the schema.
"""

_OUTPUT_SCHEMA = {
    "equation_id": "string",
    "block_id": "string",
    "section_id": "string or null",
    "section_title": "string or null",
    "label": "string or null",
    "text": "equation text",
    "latex": "string or null",
    "plain_text": "string or null",
    "equation_role": {
        "primary": "one allowed equation role",
        "secondary": ["allowed equation roles"],
        "confidence": 0.0,
        "reason": "string",
    },
    "defined_symbols": [
        {
            "symbol": "string",
            "definition_status": "defined | used | redefined | unknown",
            "evidence_text": "string or null",
        }
    ],
    "local_assumptions": [
        {"text": "string", "source_block_ids": ["block ids"]}
    ],
    "derivation_links": {
        "from_equations": ["eq_1_2"],
        "to_equations": [],
        "linked_text_spans": [
            {"span_id": "span id", "relation": "introduced_by | explained_by | derived_from_text | qualified_by | normalized_by_text"}
        ],
    },
    "summary": "short semantic summary",
    "review_flags": ["optional allowed review flags"],
}


class EquationSemanticsPromptFactory:
    def build_messages(
        self,
        llm_input: EquationLLMInput,
        cartridge: CartridgeContext | None = None,
    ) -> list[dict]:
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": self._build_user_content(llm_input, cartridge)},
        ]

    def build_repair_messages(
        self,
        llm_input: EquationLLMInput,
        previous_output: dict,
        issues: list,
        cartridge: CartridgeContext | None = None,
    ) -> list[dict]:
        issue_text = "\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        )
        content = (
            self._build_user_content(llm_input, cartridge)
            + "\n\n## Previous Output\n"
            + json.dumps(previous_output, ensure_ascii=False, indent=2)
            + "\n\n## Validation Issues\n"
            + issue_text
            + "\nReturn corrected JSON for this same equation."
        )
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": content},
        ]

    def _build_user_content(
        self,
        llm_input: EquationLLMInput,
        cartridge: CartridgeContext | None,
    ) -> str:
        parts: list[str] = []
        parts.append("## Task")
        parts.append(
            "Infer the local semantic role and dependencies of this equation.\n"
            "Return ONLY JSON matching the output schema."
        )

        parts.append("\n## Equation Context")
        parts.append(f"document_id: {llm_input.document_id}")
        parts.append(f"equation_id: {llm_input.equation_id}")
        parts.append(f"block_id: {llm_input.block_id}")
        parts.append(f"section_id: {llm_input.section_id}")
        if llm_input.section_title:
            parts.append(f"section_title: {llm_input.section_title}")
        if llm_input.backbone_block_type:
            parts.append(f"backbone_block_type: {llm_input.backbone_block_type}")
        parts.append(f"label: {llm_input.label}")

        if llm_input.prev_texts:
            parts.append("\n## Previous Text Blocks")
            parts.extend(llm_input.prev_texts)
        parts.append("\n## Equation Text")
        parts.append(llm_input.equation_text)
        if llm_input.plain_text:
            parts.append("\n## Plain Text")
            parts.append(llm_input.plain_text)
        if llm_input.next_texts:
            parts.append("\n## Next Text Blocks")
            parts.extend(llm_input.next_texts)
        if llm_input.nearby_span_annotations:
            parts.append("\n## Nearby Role-Labeled Spans")
            parts.append(json.dumps(llm_input.nearby_span_annotations, ensure_ascii=False, indent=2))

        if llm_input.normalized_terms:
            parts.append("\n## Cartridge Normalized Terms")
            for term in llm_input.normalized_terms[:20]:
                aliases = term.get("aliases", [])
                line = f"- {term.get('canonical', '')}"
                if aliases:
                    line += f" (aliases: {', '.join(str(a) for a in aliases[:5])})"
                if term.get("hints"):
                    line += f" hints: {term.get('hints')}"
                parts.append(line)
        if cartridge and cartridge.notation_patterns:
            parts.append("\n## Cartridge Notation Patterns")
            parts.append(json.dumps(cartridge.notation_patterns[:20], ensure_ascii=False))

        parts.append("\n## Output Schema")
        parts.append(json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        parts.append("\n## Allowed Values")
        parts.append(f"equation roles: {', '.join(EQUATION_ROLES)}")
        parts.append(f"definition statuses: {', '.join(DEFINITION_STATUSES)}")
        parts.append(f"linked text relations: {', '.join(LINKED_TEXT_RELATIONS)}")
        parts.append(f"review flags: {', '.join(REVIEW_FLAGS)}")
        parts.append("\n## Constraints")
        parts.append(
            "- Use local context; do not infer a role from equation shape alone\n"
            "- equation_transformation should include from_equations when context supports it\n"
            "- equation_definition should include defined_symbols when context supports it\n"
            "- If context is missing, add broken_context or ambiguous_role review flag\n"
            "- confidence must be between 0.0 and 1.0\n"
            "- Return ONLY valid JSON, no markdown fences"
        )
        return "\n".join(parts)
