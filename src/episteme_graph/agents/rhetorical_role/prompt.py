"""Prompt construction for RhetoricalRoleAgent."""
from __future__ import annotations

import json

from .schema import ROLE_LABELS, CartridgeContext, RoleLLMInput

_SYSTEM_CONTENT = """\
You are analyzing a scientific paper chunk to identify rhetorical and logical roles.

Your task is NOT to summarize the text.
Your task is to segment the chunk into role-bearing spans and label each span with \
one or more rhetorical/logical roles.

Prioritize:
1. Role separation at the level useful for downstream claim extraction
2. Exclusion of figure narration, section meta, and prior-work narration from core \
claim candidates
3. Preserving reusable content-bearing spans
4. Preferring cartridge extraction hints, aliases, and notation patterns when available
5. Returning valid JSON only

Rules:
- Return ONLY valid JSON matching the output schema.
- Use multiple role_labels if needed.
- Do not over-segment unless the chunk clearly contains multiple logical functions.
- If uncertain, use unknown or multiple roles and lower confidence.
- is_claim_candidate and is_reject_candidate must not both be true.
- role_labels must come from the allowed role vocabulary.
"""

_OUTPUT_SCHEMA = {
    "block_id": "string",
    "section_id": "string or null",
    "backbone_block_type": "string or null",
    "span_annotations": [
        {
            "span_id": "span_001",
            "text": "exact substring from block_text",
            "char_start": 0,
            "char_end": 10,
            "role_labels": ["one or more allowed role labels"],
            "is_claim_candidate": True,
            "is_reject_candidate": False,
            "confidence": 0.0,
            "reason": "short reason grounded in the text",
        }
    ],
}


class RhetoricalRolePromptFactory:
    def build_messages(
        self,
        llm_input: RoleLLMInput,
        cartridge: CartridgeContext | None = None,
    ) -> list[dict]:
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": self._build_user_content(llm_input, cartridge)},
        ]

    def build_repair_messages(
        self,
        llm_input: RoleLLMInput,
        previous_output: dict,
        issues: list,
        cartridge: CartridgeContext | None = None,
    ) -> list[dict]:
        issue_text = "\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        )
        user_content = (
            self._build_user_content(llm_input, cartridge)
            + "\n\n## Previous Output\n"
            + json.dumps(previous_output, ensure_ascii=False, indent=2)
            + "\n\n## Validation Issues\n"
            + issue_text
            + "\nReturn corrected JSON for this same block."
        )
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": user_content},
        ]

    def _build_user_content(
        self,
        llm_input: RoleLLMInput,
        cartridge: CartridgeContext | None,
    ) -> str:
        parts: list[str] = []

        parts.append("## Task")
        parts.append(
            "Segment the current chunk into rhetorical/logical spans and label each span.\n"
            "Also mark preliminary claim candidates and reject candidates.\n"
            "Return ONLY JSON matching the output schema."
        )

        parts.append("\n## Document Context")
        parts.append(f"document_id: {llm_input.document_id}")
        parts.append(f"block_id: {llm_input.block_id}")
        parts.append(f"section_id: {llm_input.section_id}")
        if llm_input.section_title:
            parts.append(f"section_title: {llm_input.section_title}")
        if llm_input.backbone_block_type:
            parts.append(f"backbone_block_type: {llm_input.backbone_block_type}")
        if llm_input.headline_claim:
            parts.append(f"headline_claim: {llm_input.headline_claim}")

        if llm_input.prev_text:
            parts.append("\n## Previous Context")
            parts.append(llm_input.prev_text)
        parts.append("\n## Current Block Text")
        parts.append(llm_input.block_text)
        if llm_input.next_text:
            parts.append("\n## Next Context")
            parts.append(llm_input.next_text)

        if llm_input.normalized_terms:
            parts.append("\n## Cartridge Hints / Normalized Terms")
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

        parts.append("\n## Allowed Role Labels")
        parts.append(", ".join(ROLE_LABELS))

        parts.append("\n## Constraints")
        parts.append(
            "- span text must be an exact substring of Current Block Text\n"
            "- char_start and char_end are zero-based offsets into Current Block Text\n"
            "- figure/table/prior-work/meta spans should usually be reject candidates\n"
            "- content-bearing definition/relation/assumption/derivation/correction/"
            "uncertainty/result spans should usually be claim candidates\n"
            "- Return ONLY valid JSON, no markdown fences"
        )

        return "\n".join(parts)
