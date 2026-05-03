"""Prompt construction for ThesisReconstructionAgent."""
from __future__ import annotations

import json

from .schema import GRAPH_RELATIONS, SUPPORT_SECTIONS, SUPPORT_TYPES, CartridgeContext, ThesisLLMInput

_SYSTEM_CONTENT = """\
You are reconstructing the thesis structure of a scientific paper.

Your task is NOT to write a summary.
Your task is to identify the paper's central thesis and organize the major supporting elements into a structured argumentative backbone.

Focus on:
1. the central thesis
2. direct supports for that thesis
3. assumptions, derivation core, corrections, uncertainties, diagnostic consequences, and future bottlenecks
4. excluding prior-work narration, figure narration, and meta discourse from the core thesis structure
5. preferring cartridge-normalized terminology when available

Be conservative and evidence-based. If multiple thesis formulations are plausible, provide one primary thesis and alternatives.
Return ONLY valid JSON matching the schema.
"""

_OUTPUT_SCHEMA = {
    "document_id": "string",
    "thesis_version": "v1",
    "cartridge_id": "string or null",
    "central_thesis": {
        "text": "string",
        "claim_ids": [],
        "equation_ids": [],
        "evidence_block_ids": [],
        "reason": "string",
        "confidence": 0.0,
    },
    "alternative_theses": [
        {"text": "string", "reason": "string", "confidence": 0.0}
    ],
    "support_structure": {
        "direct_supports": [],
        "assumptions": [],
        "derivation_core": [],
        "correction_sources": [],
        "uncertainty_sources": [],
        "diagnostic_consequences": [],
        "future_requirements": [],
    },
    "excluded_from_core": [
        {"category": "prior_work | meta | figure_narration", "claim_ids": [], "reason": "string"}
    ],
    "thesis_graph_hints": [
        {"from": "central_thesis", "to": "support:derivation_core:0", "relation": "supported_by"}
    ],
    "review_notes": [],
    "confidence": 0.0,
}


class ThesisReconstructionPromptFactory:
    def build_messages(
        self,
        llm_input: ThesisLLMInput,
        cartridge: CartridgeContext | None = None,
    ) -> list[dict]:
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": self._build_user_content(llm_input, cartridge)},
        ]

    def build_repair_messages(
        self,
        llm_input: ThesisLLMInput,
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
            + "\nReturn corrected JSON."
        )
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": content},
        ]

    def _build_user_content(
        self,
        llm_input: ThesisLLMInput,
        cartridge: CartridgeContext | None,
    ) -> str:
        payload = {
            "document_id": llm_input.document_id,
            "paper_goal": llm_input.paper_goal,
            "central_question": llm_input.central_question,
            "headline_claim": llm_input.headline_claim,
            "logical_blocks": llm_input.logical_blocks,
            "accepted_claims": llm_input.accepted_claims,
            "major_equations": llm_input.major_equations,
            "excluded_regions": llm_input.excluded_regions,
        }
        parts = [
            "## Task",
            "Reconstruct the paper's central thesis and support structure. Return ONLY JSON.",
            "\n## Input Materials",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
        if llm_input.normalized_terms:
            parts.append("\n## Cartridge Normalized Terms")
            for term in llm_input.normalized_terms[:24]:
                aliases = term.get("aliases", [])
                line = f"- {term.get('canonical', '')}"
                if aliases:
                    line += f" (aliases: {', '.join(str(a) for a in aliases[:5])})"
                parts.append(line)
        if cartridge and cartridge.notation_patterns:
            parts.append("\n## Cartridge Notation Patterns")
            parts.append(json.dumps(cartridge.notation_patterns[:20], ensure_ascii=False))
        parts.extend([
            "\n## Output Schema",
            json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2),
            "\n## Allowed Values",
            f"support sections: {', '.join(SUPPORT_SECTIONS)}",
            f"support_type: {', '.join(SUPPORT_TYPES)}",
            f"graph relations: {', '.join(GRAPH_RELATIONS)}",
            "\n## Constraints",
            "- central_thesis must not be prior-work, figure narration, or section meta\n"
            "- direct_supports must contain at least one evidence-backed entry\n"
            "- prior_work/meta/figure narration should go to excluded_from_core\n"
            "- confidence values must be between 0.0 and 1.0\n"
            "- Return ONLY valid JSON, no markdown fences",
        ])
        return "\n".join(parts)
