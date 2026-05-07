"""Prompt construction for EquationSemanticsAgent.

Issue #245: 新スキーマ (EquationRecord) の LLM 出力形式に合わせて更新。
reconstruction が必要な場合は reconstruction ブロックを出力するよう指示。
"""
from __future__ import annotations

import json

from .schema import (
    DEFINITION_STATUSES,
    EQUATION_TYPES,
    LINKED_TEXT_RELATIONS,
    RECONSTRUCTION_STATUSES,
    REVIEW_FLAGS,
    SEMANTIC_STATUSES,
    CartridgeContext,
    EquationLLMInput,
)

_SYSTEM_CONTENT = """\
You are analyzing a mathematical/display equation from a scientific paper.

Your task is NOT to prove the equation.
Your task is to reconstruct the equation's local semantic role in the paper.

Focus on:
1. The equation type (definition, relation, transformation, approximation, result, constraint)
2. Which symbols are newly defined versus merely used
3. Which local assumptions or limits the equation depends on
4. Whether the equation is derived from or leads to nearby equations
5. The semantic status: source_backed (good extraction), context_inferred, reconstruction_based
6. Producing a short reusable semantic summary
7. Always provide a reconstruction block. Treat all PDF-extracted math text,
   including inline math, as untrusted/corrupted unless a trusted non-PDF
   LaTeX source is explicitly supplied.

Be conservative. If unsure, mark as unknown and set low confidence.
Return ONLY valid JSON matching the output schema.
"""

_OUTPUT_SCHEMA = {
    "equation_id": "string",
    "label": "string or null",
    "equation_type": "one of the allowed equation types",
    "secondary_types": ["additional allowed equation types"],
    "semantic_status": "source_backed | context_inferred | reconstruction_based | unknown",
    "confidence": 0.0,
    "reason": "string",
    "defined_symbols": [
        {
            "symbol": "string",
            "definition_status": "defined | used | redefined | unknown",
            "evidence_text": "string or null",
        }
    ],
    "used_symbols": ["symbol strings that are used but not defined here"],
    "assumptions": ["local assumption text"],
    "input_equation_ids": ["eq_id of equations this derives from"],
    "output_equation_ids": ["eq_id of equations derived from this"],
    "linked_text_spans": [
        {"span_id": "span id", "relation": "introduced_by | explained_by | derived_from_text | qualified_by | normalized_by_text"}
    ],
    "summary": "short semantic summary",
    "review_flags": ["optional allowed review flags"],
    "reconstruction": {
        "__note__": "Required for every PDF-derived equation candidate",
        "latex": "string or null",
        "plain_text": "string or null",
        "status": "inferred_from_context | reconstructed_from_neighbors | manually_supplied",
        "method": ["nearby_text", "section_heading", "claim_reference", "etc"],
        "supporting_refs": ["block_id or section_id used as evidence"],
        "confidence": 0.0,
        "review_required": True,
        "review_reason": ["reason strings"],
    },
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
            "Reconstruct the equation first, then infer its local semantic role and dependencies.\n"
            "Assume all math obtained from the PDF text layer is corrupted, including inline formulas.\n"
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
        parts.append(f"extraction_status: {llm_input.extraction_status}")
        parts.append(
            "\n** Mandatory reconstruction policy: the PDF text layer is untrusted for math. "
            f"extraction_status={llm_input.extraction_status}. "
            "You MUST include a 'reconstruction' block in your output for every equation. "
            "The original broken equation text is hidden to avoid copying corrupted glyphs. "
            "Reconstruct LaTeX from the logical flow, variable definitions, equation label, surrounding prose, and neighboring equation references. "
            "If the exact equation cannot be reconstructed, return latex=null/plain_text=null, set semantic_status='unknown', confidence<=0.3, and add review flags. "
            "Never mark semantic_status='source_backed' for PDF-derived math. "
            "Do NOT store reconstructed content in top-level source fields. **"
        )

        if llm_input.prev_texts:
            parts.append("\n## Previous Text Blocks")
            parts.extend(llm_input.prev_texts)

        parts.append("\n## Equation Text")
        parts.append("[OMITTED - PDF math text is treated as corrupted. Reconstruct from context only.]")

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
        parts.append(f"equation_type: {', '.join(EQUATION_TYPES)}")
        parts.append(f"semantic_status: {', '.join(SEMANTIC_STATUSES)}")
        parts.append(f"definition_statuses: {', '.join(DEFINITION_STATUSES)}")
        parts.append(f"linked text relations: {', '.join(LINKED_TEXT_RELATIONS)}")
        parts.append(f"review flags: {', '.join(REVIEW_FLAGS)}")
        parts.append(f"reconstruction statuses: {', '.join(RECONSTRUCTION_STATUSES)} (exclude 'none')")
        parts.append("\n## Constraints")
        parts.append(
            "- Use local context; do not infer a role from corrupted equation text alone\n"
            "- equation_type=transformation → include input_equation_ids when context supports it\n"
            "- equation_type=definition → include defined_symbols when context supports it\n"
            "- If context is missing, add broken_context or ambiguous_role review flag\n"
            "- confidence must be between 0.0 and 1.0\n"
            "- reconstruction block: method and supporting_refs MUST be non-empty\n"
            "- Do NOT set semantic_status=source_backed for PDF-derived equations\n"
            "- Return ONLY valid JSON, no markdown fences"
        )
        return "\n".join(parts)
