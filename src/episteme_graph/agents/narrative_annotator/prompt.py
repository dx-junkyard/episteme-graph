"""Prompt construction for NarrativeAnnotator (issue #360)."""
from __future__ import annotations

import json

from episteme_graph.agents.generation_language import apply_generation_language

from .input_builder import prompt_edges, prompt_nodes
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
- Ground every sentence in the provided node labels, descriptions, teaching
  takeaways (claim text of the steps a stage aggregates), equation numbers and
  thesis text. Do not invent results that are not in the material.
- Reuse the provided teaching takeaways where they fit; rephrase for flow.
- Nodes and edges are addressed by short handles (N1, E1, ...). Use a handle
  only in the "ref" field; never write handles, identifiers or counts into the
  reader-facing text.
- Write for a graduate student reading the graph for the first time.
- Plain declarative sentences. No marketing language, no "this amazing paper".
- Every node / edge ref you return must come from the input lists.
- Give each annotation a short reason and a confidence between 0.0 and 1.0.
- Return ONLY valid JSON matching the provided schema.
"""

_OUTPUT_SCHEMA = {
    "graph_summary": "3-5 sentences (central thesis -> support structure)",
    "node_narratives": [
        {
            "ref": "main node ref from the input (N1, N2, ...)",
            "narrative_role": "1-2 sentences: role of this stage in the argument",
            "reason": "which input material grounds this text",
            "confidence": 0.0,
        }
    ],
    "edge_narratives": [
        {
            "ref": "main edge ref from the input (E1, E2, ...)",
            "transition_text": "1 sentence: what this edge hands to the next stage",
            "reason": "which input material grounds this text",
            "confidence": 0.0,
        }
    ],
    "review_notes": ["optional short notes for the teacher"],
    "confidence": 0.0,
}


#: 解析 run の生成言語（run options ``language``）で書かせる、生成した文章のフィールド。
#: 論文の逐語・記号・ID・語彙値は含めない（``generation_language`` の不変条項）。
GENERATED_PROSE_FIELDS: tuple[str, ...] = (
    "graph_summary",
    "node_narratives[].narrative_role",
    "node_narratives[].reason",
    "edge_narratives[].transition_text",
    "edge_narratives[].reason",
    "review_notes",
)


class NarrativePromptFactory:
    #: 解析 run の生成言語（``ja`` / ``en``）。``None`` なら prompt は従来と同一。
    language: str | None = None

    def __init__(self, language: str | None = None) -> None:
        self.language = language

    def build_messages(self, *args, **kwargs) -> list[dict]:
        return apply_generation_language(
            self._base_messages(*args, **kwargs), self.language, GENERATED_PROSE_FIELDS
        )

    def build_repair_messages(self, *args, **kwargs) -> list[dict]:
        return apply_generation_language(
            self._base_repair_messages(*args, **kwargs), self.language, GENERATED_PROSE_FIELDS
        )

    def _base_messages(self, llm_input: NarrativeLLMInput, cartridge=None) -> list[dict]:
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": self._build_user_content(llm_input)},
        ]

    def _base_repair_messages(
        self,
        llm_input: NarrativeLLMInput,
        previous_output: dict,
        issues: list,
        cartridge=None,
    ) -> list[dict]:
        issue_text = _with_refs("\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        ), llm_input)
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
        if llm_input.central_thesis_text:
            parts.append("\n## Central Thesis")
            parts.append(llm_input.central_thesis_text)
        parts.append("\n## Main Nodes (fixed; annotate each)")
        parts.append(json.dumps(prompt_nodes(llm_input), ensure_ascii=False, indent=2))
        parts.append("\n## Main Edges (fixed; annotate each)")
        parts.append(json.dumps(prompt_edges(llm_input), ensure_ascii=False, indent=2))
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
            "- each ref must exist in the inputs\n"
            "- Return ONLY valid JSON, no markdown fences"
        )
        return "\n".join(parts)


def _with_refs(text: str, llm_input: NarrativeLLMInput) -> str:
    """Show validator messages with the node / edge handles, not internal IDs (IK-0753)."""
    pairs = [
        (str(n.get("component_id") or ""), str(n.get("ref") or ""))
        for n in llm_input.main_nodes
    ] + [
        (str(e.get("edge_id") or ""), str(e.get("ref") or ""))
        for e in llm_input.main_edges
    ]
    for internal_id, ref in sorted(pairs, key=lambda p: -len(p[0])):
        if internal_id and ref:
            text = text.replace(internal_id, ref)
    return text
