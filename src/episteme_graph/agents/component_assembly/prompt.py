"""Prompt construction for ComponentAssemblyAgent."""
from __future__ import annotations

import json

from .schema import ASSEMBLY_HINT_TYPES, ComponentAssemblyLLMInput

_SYSTEM_CONTENT = """\
You are assembling reusable knowledge components from scientific paper analysis outputs.

Your task is NOT to summarize sections.
Your task is to construct reusable components with explicit inputs, outputs,
preconditions, cautions, dependencies, and an INTERNAL FLOW that connects them.

Important constraints:
- Use only component types allowed by the active cartridge or explicit core fallbacks.
- Use only dependency labels from the allowed vocabulary.
- Do not invent new component taxonomies.
- Components must be reusable units, not topical or section summaries.
- Use accepted claims, equation semantics, thesis structure, and DSL graph evidence.
- Do not let prior work or meta discourse dominate component cores.

internal_flow requirement:
- For RelationComponent / PaperRelationComponent / CorrectionComponent /
  DiagnosticComponent / MethodComponent, internal_flow MUST NOT be empty.
- Each internal_flow step is {"from": "<symbol/equation/claim id>",
  "relation": "<short verb such as combine_with / normalize / take_limit /
  apply_correction / measure>", "to": "<symbol/equation/claim id>"}.
- internal_flow should explain how the inputs are processed/combined into the
  outputs. Avoid restating the summary; describe the actual operations.
- For components with multiple inputs or outputs, internal_flow is required
  to make the wiring explicit.

Return ONLY valid JSON matching the schema.
"""

_OUTPUT_SCHEMA = {
    "document_id": "string",
    "components_version": "v1",
    "cartridge_id": "string or null",
    "components": [
        {
            "component_id": "comp_001",
            "component_type": "allowed component type",
            "label": "short label",
            "summary": "reusable component summary",
            "inputs": [{"name": "string", "node_refs": [], "claim_ids": [], "equation_ids": []}],
            "outputs": [{"name": "string", "node_refs": [], "claim_ids": [], "equation_ids": []}],
            "preconditions": [{"text": "string", "claim_ids": [], "equation_ids": []}],
            "cautions": [{"text": "string", "claim_ids": [], "equation_ids": []}],
            "dependencies": [{"dependency_type": "allowed dependency type", "component_refs": [], "reason": "string"}],
            "internal_flow": [
                {"from": "string", "relation": "string", "to": "string"}
            ],
            "evidence_refs": {
                "claim_ids": [],
                "equation_ids": [],
                "thesis_refs": [],
                "dsl_refs": {"node_ids": [], "edge_ids": []}
            },
            "reason": "string",
            "confidence": 0.0,
            "review_notes": []
        }
    ],
    "assembly_hints": [
        {"hint_type": "candidate_core_component", "component_ids": ["comp_001"], "reason": "string"}
    ],
    "review_notes": [],
    "confidence": 0.0
}


class ComponentAssemblyPromptFactory:
    def build_messages(self, llm_input: ComponentAssemblyLLMInput) -> list[dict]:
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": self._build_user_content(llm_input)},
        ]

    def build_repair_messages(
        self,
        llm_input: ComponentAssemblyLLMInput,
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

    def _build_user_content(self, llm_input: ComponentAssemblyLLMInput) -> str:
        payload = {
            "document_id": llm_input.document_id,
            "accepted_claims": llm_input.accepted_claims,
            "equations": llm_input.equations,
            "thesis_nodes": llm_input.thesis_nodes,
            "dsl_nodes": llm_input.dsl_nodes,
            "dsl_edges": llm_input.dsl_edges,
            "normalized_terms": llm_input.normalized_terms,
        }
        return "\n".join([
            "## Task",
            "Assemble reusable knowledge components. Return ONLY JSON.",
            "\n## Input Materials",
            json.dumps(payload, ensure_ascii=False, indent=2),
            "\n## Allowed Component Types",
            ", ".join(llm_input.allowed_component_types),
            "\n## Allowed Dependency Types",
            ", ".join(llm_input.allowed_dependency_types),
            "\n## Output Schema",
            json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2),
            "\n## Assembly Hint Types",
            ", ".join(ASSEMBLY_HINT_TYPES),
            "\n## Constraints",
            "- Avoid section-summary components\n"
            "- Each strong component should include evidence_refs\n"
            "- Derivation-like components need outputs and usually preconditions\n"
            "- Correction/uncertainty/diagnostic components should remain distinct when evidence supports separation\n"
            "- Relation/Correction/Diagnostic/Method components MUST include internal_flow\n"
            "- internal_flow explains how inputs are combined/transformed into outputs\n"
            "- Return ONLY valid JSON, no markdown fences",
        ])
