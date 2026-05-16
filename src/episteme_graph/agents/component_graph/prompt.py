"""Prompt construction for ComponentGraphAgent (Issue #266)."""
from __future__ import annotations

import json

from .schema import ComponentGraphLLMInput

_SYSTEM_CONTENT = """\
You are building a logical dependency graph between knowledge components extracted from a scientific paper.

Your task: infer directed edges between components based on four evidence sources provided below.
DO NOT summarize the paper. DO NOT invent new components.
Only connect the components listed in component_ids using the valid_edge_types provided.

Edge inference rules:
1. REQUIRES: Component A requires Component B as a prerequisite (B must exist/hold for A to apply).
2. PRODUCES_FOR: Component A produces output that is directly consumed as input by Component B.
3. TRANSFORMS: Component A applies a mathematical or logical transformation whose result feeds Component B.
4. DERIVES_FROM: Component A is mathematically derived from Component B (equation derivation flow).
5. QUALIFIES: Component A adds conditions, corrections, or uncertainty bounds to Component B.
6. ENABLES: Component A enables Component B to operate (looser form of REQUIRES).
7. INHIBITS: Component A contradicts or limits the validity of Component B.
8. CHECKED_BY: Component A's output is validated/diagnosed by Component B.
9. DEFINES: Component A defines a concept or quantity that Component B uses.
10. CONFLICTS_WITH: Component A is mutually exclusive with or contradicts Component B.
11. RELATED_TO: General non-directional association (use as last resort).

Inference strategy (in priority order):
1. Check explicit `dependencies` in Material 1 — these yield declared edges.
2. Check if Component A's `outputs` match Component B's `inputs` or `preconditions` — yields PRODUCES_FOR / ENABLES.
3. Use cross-component DSL edges in Material 2 — core_predicate maps to edge_type.
4. Use derivation chains in Material 3 — sequence of linked_component_ids yields DERIVES_FROM / TRANSFORMS.
5. Use evidence texts in Material 4 to select appropriate evidence_claims.

Output constraints (CRITICAL):
- source and target MUST be valid component_ids from the component_ids list.
- edge_type MUST be one of valid_edge_types.
- No self-loops (source != target).
- evidence_claims MUST reference claim_ids that appear in Materials 1–4.
- Do NOT add duplicate edges with the same (source, target, edge_type).
- Assign a sequential edge_id like "component_edge_0001".
- support_status: use "dependency_declared" for explicit dependency edges,
  "io_matched" for input/output matches, "dsl_cross_edge" for DSL-derived edges,
  "derivation_linked" for derivation chain edges, "llm_inferred" for all others.

Return ONLY valid JSON matching the schema below. No markdown fences.
"""

_OUTPUT_SCHEMA = {
    "edges": [
        {
            "edge_id": "component_edge_0001",
            "source": "<component_id>",
            "target": "<component_id>",
            "edge_type": "<one of valid_edge_types>",
            "support_status": "<dependency_declared|io_matched|dsl_cross_edge|derivation_linked|llm_inferred>",
            "evidence_claims": ["<claim_id or empty list>"],
            "reasoning": "<concise explanation of why this edge exists (debug only)>",
            "confidence": 0.8,
        }
    ]
}


class ComponentGraphPromptFactory:
    def build_messages(self, llm_input: ComponentGraphLLMInput) -> list[dict]:
        user_content = self._build_user_content(llm_input)
        return [
            {"role": "user", "content": f"{_SYSTEM_CONTENT}\n\n{user_content}"},
        ]

    def build_repair_messages(
        self,
        llm_input: ComponentGraphLLMInput,
        previous_output: dict,
        issues: list,
    ) -> list[dict]:
        issue_lines = "\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        )
        repair_note = (
            f"Your previous output had validation errors:\n{issue_lines}\n\n"
            "Please fix these issues and return corrected JSON.\n\n"
            f"Previous output:\n{json.dumps(previous_output, ensure_ascii=False)[:3000]}\n\n"
        )
        user_content = repair_note + self._build_user_content(llm_input)
        return [
            {"role": "user", "content": f"{_SYSTEM_CONTENT}\n\n{user_content}"},
        ]

    @staticmethod
    def _build_user_content(llm_input: ComponentGraphLLMInput) -> str:
        sections: list[str] = []

        sections.append(
            f"document_id: {llm_input.document_id}\n"
            f"cartridge_id: {llm_input.cartridge_id or 'none'}\n"
            f"valid_edge_types: {json.dumps(llm_input.valid_edge_types)}\n"
            f"component_ids: {json.dumps(llm_input.component_ids)}"
        )

        sections.append(
            "=== Material 1: Components (I/O, dependencies) ===\n"
            + json.dumps(llm_input.components, ensure_ascii=False, indent=1)
        )

        if llm_input.cross_component_dsl_edges:
            sections.append(
                "=== Material 2: Cross-component DSL edges ===\n"
                + json.dumps(llm_input.cross_component_dsl_edges, ensure_ascii=False, indent=1)
            )
        else:
            sections.append("=== Material 2: Cross-component DSL edges ===\n(none)")

        if llm_input.multi_component_derivations:
            sections.append(
                "=== Material 3: Derivation chains spanning multiple components ===\n"
                + json.dumps(llm_input.multi_component_derivations, ensure_ascii=False, indent=1)
            )
        else:
            sections.append("=== Material 3: Multi-component derivation chains ===\n(none)")

        evidence_block: list[str] = []
        if llm_input.claim_texts:
            evidence_block.append("Claims:")
            for cid, text in llm_input.claim_texts.items():
                evidence_block.append(f"  {cid}: {text}")
        if llm_input.evidence_texts:
            evidence_block.append("Evidence:")
            for eid, text in llm_input.evidence_texts.items():
                evidence_block.append(f"  {eid}: {text}")
        sections.append(
            "=== Material 4: Evidence texts ===\n"
            + ("\n".join(evidence_block) if evidence_block else "(none)")
        )

        sections.append(
            "=== Expected output schema ===\n"
            + json.dumps(_OUTPUT_SCHEMA, indent=2)
        )

        sections.append(
            "Infer the complete edge list. Return ONLY JSON."
        )

        return "\n\n".join(sections)
