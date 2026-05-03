"""Prompt construction for DSLLinkingAgent."""
from __future__ import annotations

import json

from .schema import (
    CORE_PREDICATES,
    DOMAIN_VERBS,
    GRAPH_HINT_TYPES,
    NODE_TYPES,
    POLARITIES,
    SOURCE_KINDS,
    DSLLLMInput,
)

_SYSTEM_CONTENT = """\
You are constructing a lightweight DSL graph from scientific paper analysis outputs.

Your task is NOT to build final reusable components.
Your task is to create a compact intermediate graph of nodes and edges that captures the paper's logical structure.

Use:
- nodes for core entities, relations, approximations, uncertainties, diagnostics, and thesis proxies
- edges with core predicates, domain verbs, and polarity
- evidence references whenever possible

Keep the DSL lightweight. Do not include review metadata, maturity, or pedagogical commentary.
Do not let prior work or meta discourse dominate the core graph.
If uncertain, use lower confidence or polarity '?' rather than inventing strong edges.
Return ONLY valid JSON matching the schema.
"""

_OUTPUT_SCHEMA = {
    "document_id": "string",
    "dsl_version": "v1",
    "nodes": [
        {
            "node_id": "n1",
            "node_type": "Relation",
            "node_value": "short normalized node value",
            "source_kind": "claim | equation | thesis | mixed",
            "source_refs": {
                "claim_ids": [],
                "equation_ids": [],
                "thesis_refs": []
            },
            "reason": "string",
            "confidence": 0.0
        }
    ],
    "edges": [
        {
            "edge_id": "e1",
            "from_node_id": "n1",
            "to_node_id": "n2",
            "core_predicate": "REQUIRES",
            "domain_verb": "assumes",
            "polarity": "+",
            "evidence_refs": {
                "claim_ids": [],
                "equation_ids": [],
                "thesis_refs": []
            },
            "reason": "string",
            "confidence": 0.0
        }
    ],
    "graph_hints": [
        {"hint_type": "component_seed", "node_ids": ["n1", "n2"], "reason": "string"}
    ],
    "review_notes": [],
    "confidence": 0.0
}


class DSLLinkingPromptFactory:
    def build_messages(self, llm_input: DSLLLMInput) -> list[dict]:
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": self._build_user_content(llm_input)},
        ]

    def build_repair_messages(
        self,
        llm_input: DSLLLMInput,
        previous_output: dict,
        issues: list,
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
            + "\nReturn corrected JSON."
        )
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": content},
        ]

    def _build_user_content(self, llm_input: DSLLLMInput) -> str:
        payload = {
            "document_id": llm_input.document_id,
            "accepted_claims": llm_input.accepted_claims,
            "equations": llm_input.equations,
            "thesis_nodes": llm_input.thesis_nodes,
            "excluded_from_core": llm_input.excluded_from_core,
        }
        return "\n".join([
            "## Task",
            "Construct a lightweight DSL graph from the accepted claims, equation semantics, and thesis structure. Return ONLY JSON.",
            "\n## Input Materials",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "\n## Output Schema",
            json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2),
            "\n## Allowed Values",
            f"node_type: {', '.join(NODE_TYPES)}",
            f"source_kind: {', '.join(SOURCE_KINDS)}",
            f"core_predicate: {', '.join(CORE_PREDICATES)}",
            f"domain_verb preferred values: {', '.join(DOMAIN_VERBS)}",
            f"polarity: {', '.join(POLARITIES)}",
            f"graph_hints[].hint_type: {', '.join(GRAPH_HINT_TYPES)}",
            "\n## Constraints",
            "- Do not assume one claim equals one node; compress or split when appropriate\n"
            "- Include equation and thesis evidence when they clarify edges\n"
            "- Strong edges should include evidence_refs\n"
            "- Avoid self-loops\n"
            "- Return ONLY valid JSON, no markdown fences",
        ])
