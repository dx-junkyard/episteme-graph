"""Prompt construction for NarrativeAnnotator (issue #360)."""
from __future__ import annotations

import json

from .schema import (
    MAX_GRAPH_SUMMARY_CHARS,
    MAX_NARRATIVE_ROLE_CHARS,
    MAX_TRANSITION_TEXT_CHARS,
    NarrativeLLMInput,
)

_SYSTEM_CONTENT = """\
You are writing the reading layer for a theory-operation graph extracted from a
scientific paper. The graph structure is FIXED — you never add, remove, merge,
or reorder nodes or edges. You only write short reader-facing text:

1. graph_summary — 3–5 sentences: the paper's central thesis and how the graph
   stages support it, in reading order.
2. node_narratives[] — for each main node: narrative_role (1–2 sentences) —
   what this stage contributes to the paper's argument.
3. edge_narratives[] — for each main edge: transition_text (1 sentence) —
   what the source stage hands to the target stage.

Rules:
- Ground every sentence in the provided node descriptions, teaching takeaways,
  and thesis text. Do not invent results that are not in the material.
- Reuse the provided teaching takeaways where they fit; rephrase for flow.
- Write for a graduate student reading the graph for the first time.
- Plain declarative sentences. No marketing language, no "this amazing paper".
- Every component_id / edge_id you mention must come from the input lists.
- Give each annotation a short reason and a confidence between 0.0 and 1.0.
- Return ONLY valid JSON matching the provided schema.
"""

_OUTPUT_SCHEMA = {
    "graph_summary": "3-5 sentences (central thesis -> support structure)",
    "node_narratives": [
        {
            "component_id": "main node component_id from the input",
            "narrative_role": "1-2 sentences: role of this stage in the argument",
            "reason": "which input material grounds this text",
            "confidence": 0.0,
        }
    ],
    "edge_narratives": [
        {
            "edge_id": "main edge edge_id from the input",
            "transition_text": "1 sentence: what this edge hands to the next stage",
            "reason": "which input material grounds this text",
            "confidence": 0.0,
        }
    ],
    "review_notes": ["optional short notes for the teacher"],
    "confidence": 0.0,
}


class NarrativePromptFactory:
    def build_messages(self, llm_input: NarrativeLLMInput, cartridge=None) -> list[dict]:
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": self._build_user_content(llm_input)},
        ]

    def build_repair_messages(
        self,
        llm_input: NarrativeLLMInput,
        previous_output: dict,
        issues: list,
        cartridge=None,
    ) -> list[dict]:
        issue_text = "\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        )
        content = (
            self._build_user_content(llm_input)
            + "\n\n## Previous Output\n"
            + json.dumps(previous_output, ensure_ascii=False, indent=2)
            + "\n\n## Validation Issues\n"
            + issue_text
            + "\nReturn corrected JSON for the same graph."
        )
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": content},
        ]

    @staticmethod
    def _build_user_content(llm_input: NarrativeLLMInput) -> str:
        parts: list[str] = []
        parts.append("## Task")
        parts.append(
            "Write graph_summary, node_narratives, and edge_narratives for the "
            "main theory-operation graph below. Return ONLY JSON."
        )
        parts.append(f"\ndocument_id: {llm_input.document_id}")
        if llm_input.central_thesis_text:
            parts.append("\n## Central Thesis")
            parts.append(llm_input.central_thesis_text)
        parts.append("\n## Main Nodes (fixed; annotate each)")
        parts.append(json.dumps(llm_input.main_nodes, ensure_ascii=False, indent=2))
        parts.append("\n## Main Edges (fixed; annotate each)")
        parts.append(json.dumps(llm_input.main_edges, ensure_ascii=False, indent=2))
        if llm_input.normalized_terms:
            parts.append("\n## Cartridge Normalized Terms")
            for term in llm_input.normalized_terms[:20]:
                parts.append(f"- {term.get('canonical', '')}")
        parts.append("\n## Output Schema")
        parts.append(json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        parts.append("\n## Constraints")
        parts.append(
            f"- graph_summary <= {MAX_GRAPH_SUMMARY_CHARS} characters\n"
            f"- narrative_role <= {MAX_NARRATIVE_ROLE_CHARS} characters\n"
            f"- transition_text <= {MAX_TRANSITION_TEXT_CHARS} characters\n"
            "- one node_narratives entry per main node, one edge_narratives entry per main edge\n"
            "- component_id / edge_id must exist in the inputs\n"
            "- Return ONLY valid JSON, no markdown fences"
        )
        return "\n".join(parts)
