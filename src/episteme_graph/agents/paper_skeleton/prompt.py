"""PaperSkeletonPromptFactory: LLM に渡す messages を構築する。

o1/o3-mini 互換設計: system ロールは使用するが、llm_client.py 側で
reasoning model の場合に developer ロールへ変換される。
"""
from __future__ import annotations

import json

from .schema import LOGICAL_BLOCK_TYPES, CartridgeContext, SkeletonLLMInput

_SYSTEM_CONTENT = """\
You are analyzing a scientific paper to reconstruct its logical backbone.

Your task is NOT to write a fluent summary.
Your task is to produce a structured backbone hypothesis that will help downstream \
claim extraction and component assembly.

Prioritize:
1. The paper's central problem and headline claim
2. The main logical blocks supporting that claim
3. Exclusion of prior-work narration, figure narration, and meta discourse from the \
core backbone
4. Preferring cartridge-normalized terminology when available

Rules:
- Return ONLY valid JSON that exactly matches the provided output schema.
- If uncertain, lower confidence rather than inventing unsupported structure.
- Always include reasons and evidence block IDs when possible.
- Do not include prior work or figure narration in the core headline_claim \
unless absolutely necessary.
- Each logical_block must have a block_type from the allowed list.
- supporting_subclaims may be empty if the paper has a single strong claim.
- excluded_regions should list at least one prior_work or meta region if present.
- confidence values must be between 0.0 and 1.0.
"""

_OUTPUT_SCHEMA = {
    "document_id": "string",
    "skeleton_version": "v1",
    "cartridge_id": "string or null",
    "paper_goal": {
        "text": "string — what problem this paper addresses",
        "evidence_block_ids": ["list of block_ids supporting this"],
        "reason": "string — why this was selected as the paper goal",
        "confidence": 0.0,
    },
    "central_question": {
        "text": "string — the research question",
        "evidence_block_ids": [],
        "reason": "string",
        "confidence": 0.0,
    },
    "headline_claim": {
        "text": "string — the central claim or contribution",
        "evidence_block_ids": [],
        "reason": "string — why this is the headline claim, not figure narration",
        "confidence": 0.0,
    },
    "supporting_subclaims": [
        {
            "subclaim_id": "subclaim_1",
            "text": "string",
            "role": "supporting_claim",
            "evidence_block_ids": [],
            "reason": "string",
            "confidence": 0.0,
        }
    ],
    "logical_blocks": [
        {
            "block_id": "logic_1",
            "block_type": "one of: " + " | ".join(LOGICAL_BLOCK_TYPES),
            "label": "string — short human-readable label",
            "section_ids": ["list of section_ids"],
            "evidence_block_ids": ["list of block_ids"],
            "summary": "string — what this block covers",
            "reason": "string — why this is classified as this block_type",
            "confidence": 0.0,
        }
    ],
    "excluded_regions": [
        {
            "region_type": "prior_work | meta | figure_narration | section_transition | appendix",
            "section_ids": [],
            "block_ids": [],
            "reason": "string",
        }
    ],
    "review_notes": ["list of strings — provisional observations for human review"],
    "confidence": 0.0,
}


class PaperSkeletonPromptFactory:
    def build_messages(
        self,
        llm_input: SkeletonLLMInput,
        cartridge: CartridgeContext | None = None,
    ) -> list[dict]:
        user_content = self._build_user_content(llm_input, cartridge)
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": user_content},
        ]

    def build_repair_messages(
        self,
        llm_input: SkeletonLLMInput,
        previous_output: dict,
        issues: list,
        cartridge: CartridgeContext | None = None,
    ) -> list[dict]:
        issue_text = "\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        )
        repair_note = (
            f"\n\nThe previous JSON output had validation issues:\n{issue_text}\n"
            "Please fix these issues and return corrected JSON."
        )
        user_content = self._build_user_content(llm_input, cartridge) + repair_note
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": user_content},
        ]

    def _build_user_content(
        self,
        llm_input: SkeletonLLMInput,
        cartridge: CartridgeContext | None,
    ) -> str:
        parts: list[str] = []

        parts.append("## Task")
        parts.append(
            "Given the following structured paper input, infer the logical backbone.\n"
            "Treat this as a provisional backbone hypothesis for downstream claim extraction.\n"
            "Return ONLY JSON matching the output schema below."
        )

        parts.append("\n## Document ID")
        parts.append(llm_input.document_id)

        if llm_input.title:
            parts.append("\n## Title")
            parts.append(llm_input.title)

        if llm_input.abstract_blocks:
            parts.append("\n## Abstract / Pre-section Content")
            for b in llm_input.abstract_blocks:
                parts.append(f"[{b['block_id']}] {b['text']}")

        if llm_input.section_headers:
            parts.append("\n## Section Structure")
            for s in llm_input.section_headers:
                indent = "  " * (s["level"] - 1)
                parts.append(f"{indent}- [{s['section_id']}] {s['title']} (page {s['page_start']})")

        if llm_input.representative_blocks:
            parts.append("\n## Representative Blocks per Section")
            for b in llm_input.representative_blocks:
                sec_label = b.get("section_title", "")
                parts.append(
                    f"[{b['block_id']}] ({b['block_type']}, sec: {sec_label})\n  {b['text']}"
                )

        if llm_input.conclusion_blocks:
            parts.append("\n## Conclusion / Discussion Blocks")
            for b in llm_input.conclusion_blocks:
                parts.append(f"[{b['block_id']}] {b['text']}")

        if llm_input.normalized_terms:
            parts.append("\n## Cartridge Normalized Terminology")
            parts.append(
                "Prefer these canonical terms in logical block summaries and claim text:"
            )
            for t in llm_input.normalized_terms[:20]:  # cap to avoid token explosion
                aliases = t.get("aliases", [])
                line = f"  - {t.get('canonical', '')}"
                if aliases:
                    line += f" (aliases: {', '.join(str(a) for a in aliases[:5])})"
                parts.append(line)

        parts.append("\n## Output Schema")
        parts.append(json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2))

        parts.append("\n## Constraints")
        parts.append(
            f"- logical_blocks[].block_type must be one of: {', '.join(LOGICAL_BLOCK_TYPES)}\n"
            "- headline_claim must NOT be a figure narration (e.g. 'Figure 1 shows...')\n"
            "- prior_work references must go in excluded_regions or logical_blocks with type 'prior_work'\n"
            "- All confidence values must be between 0.0 and 1.0\n"
            "- Return ONLY valid JSON, no markdown fences"
        )

        return "\n".join(parts)
