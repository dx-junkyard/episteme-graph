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

ID constraints (CRITICAL):
- For evidence_refs.claim_ids: use ONLY IDs from available_claims list.
- For evidence_refs.evidence_ids: use ONLY IDs from available_evidence list.
- For evidence_refs.equation_ids: use ONLY IDs from available_equations list.
- For evidence_refs.dsl_refs.node_ids: use ONLY IDs from available_dsl_nodes list.
- For evidence_refs.dsl_refs.edge_ids: use ONLY IDs from available_dsl_edges list.
- Do NOT generate, invent, or guess IDs not present in the available_* lists.
- A source-backed component MUST have at least one claim_id or evidence_id in evidence_refs.

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

equation role requirement:
- Classify equations by component role. Do not put every related equation into
  one undifferentiated linked_equation_ids list.
- For derivation-like RelationComponent / PaperRelationComponent / MethodComponent,
  include input_equation_ids and output_equation_ids whenever equations are available.
- Use intermediate_equation_ids for solved/substituted forms that are not the final
  reusable result, constraint_equation_ids for restrictions/consistency checks, and
  definition_equation_ids for definitions used by the component.
- If an equation is review_required, reconstructed, or cannot support claims, include
  it in review_required_equation_ids and set component review_status to
  teacher_review_required unless the component is merely cautionary.
- Bias-elimination components should include eliminated_symbols, retained_symbols,
  and internal_flow steps with concrete solve/substitute/eliminate operations.

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
                "evidence_ids": [],
                "equation_ids": [],
                "thesis_refs": [],
                "dsl_refs": {"node_ids": [], "edge_ids": []}
            },
            "linked_claim_ids": [],
            "linked_equation_ids": [],
            "linked_evidence_ids": [],
            "linked_derivation_ids": [],
            "linked_dsl_node_ids": [],
            "linked_dsl_edge_ids": [],
            "input_equation_ids": [],
            "intermediate_equation_ids": [],
            "output_equation_ids": [],
            "constraint_equation_ids": [],
            "definition_equation_ids": [],
            "review_required_equation_ids": [],
            "eliminated_symbols": [],
            "retained_symbols": [],
            "equation_confidence_summary": {
                "all_source_backed": True,
                "has_review_required": False,
                "has_reconstructed_equations": False
            },
            "review_status": "teacher_review_required",
            "teaching_takeaway": "string",
            "source_scope": {},
            "assumptions": [],
            "approximations": [],
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
        available = {
            "available_claims": llm_input.available_claims,
            "available_evidence": llm_input.available_evidence,
            "available_equations": llm_input.available_equations,
            "available_dsl_nodes": llm_input.available_dsl_nodes,
            "available_dsl_edges": llm_input.available_dsl_edges,
            "available_derivation_ids": llm_input.available_derivation_ids,
        }
        parts = [
            "## Task",
            "Assemble reusable knowledge components. Return ONLY JSON.",
            "\n## Input Materials",
            json.dumps(payload, ensure_ascii=False, indent=2),
        ]
        if any(v for v in available.values()):
            parts += [
                "\n## Available Artifact IDs (use ONLY these IDs in evidence_refs)",
                json.dumps(available, ensure_ascii=False, indent=2),
            ]
        parts += [
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
            "- Use ONLY IDs from the available_* lists above in evidence_refs\n"
            "- Do NOT invent or guess IDs not present in available_* lists\n"
            "- Derivation-like components need outputs and usually preconditions\n"
            "- Derivation-like components with equations must classify equations into input/intermediate/output/constraint/definition roles\n"
            "- Bias-elimination components must name eliminated_symbols and retained_symbols\n"
            "- Do not use can_support_claim=false or review_required equations as source-backed component outputs\n"
            "- Correction/uncertainty/diagnostic components should remain distinct when evidence supports separation\n"
            "- Relation/Correction/Diagnostic/Method components MUST include internal_flow\n"
            "- internal_flow explains how inputs are combined/transformed into outputs\n"
            "- Return ONLY valid JSON, no markdown fences",
        ]
        return "\n".join(parts)
