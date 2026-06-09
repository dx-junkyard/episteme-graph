"""Prompt construction for ComponentAssemblyAgent."""
from __future__ import annotations

import json

from .schema import ASSEMBLY_HINT_TYPES, ComponentAssemblyLLMInput

_SYSTEM_CONTENT = """\
You are assembling reusable knowledge components from scientific paper analysis outputs.

Your task is NOT to summarize sections.
Your task is to construct reusable components with explicit inputs, outputs,
preconditions, cautions, dependencies, and an INTERNAL FLOW that connects them.

Component boundaries follow THEORY STRUCTURE, not explanation structure.
1 component = 1 main theoretical operation and 1 responsibility type.
Decide boundaries from the headline claim support plan first, then by:
inputs change, outputs change, the operation changes,
assumptions change, the eliminated target changes, the derivation result changes,
the equation role changes, or the review status changes.
Components are reusable theory parts that support the paper's main claim, not
section summaries or contiguous equation sequences.
Split a component when it contains two or more of:
define, parameterize, linearize, solve, substitute, eliminate, derive, compare,
diagnose, forecast, limit. Do NOT keep a bias-parameter solution, a consistency
relation, a forecast constraint, and a validity caveat inside one component.
Each component should set "operation" to its single main operation.
Each component should set "responsibility_type" to one of:
definition, model, observation_model, observable_basis, equation_system,
derivation, constraint, application, limitation.
Each component should also set primary_operation, secondary_operations, and
split_recommendation. If multiple responsibility types are present,
split_recommendation.required must be true.
Each component must set support_role, supports_claim_ids,
support_distance_to_headline_claim, and support_kind from the claim-centered plan.

Important constraints:
- Use only component types allowed by the active cartridge or explicit core fallbacks.
- Use only dependency labels from the allowed vocabulary.
- Do not invent new component taxonomies.
- Components must be reusable theory operations, not topical or section summaries.
- Build components around how they support the headline claim. Separate
  theory_base, observable_bridge, derivation_core, result, application, and
  limitation components even when they appear in the same section.
- Background-only components must use support_kind=prerequisite.
- Bias-elimination or solve/eliminate components belong in support_role=derivation_core.
- Consistency-relation outputs belong in support_role=result.
- Forecast/diagnostic use belongs in support_role=application, not derivation_core.
- Split oversized map components into definition / model /
  observation_model / observable_basis / equation_system /
  derivation / constraint units. For example, a
  bias-to-smoothed-observables map must not mix galaxy bias, smoothing,
  skewness/kurtosis observable bases, bias-linearized equations, bias
  elimination, and final consistency relations.
- Use accepted claims, equation semantics, thesis structure, and DSL graph evidence.
- Use only atomic claims as primary support. Do not use claims with
  atomicity=composite, split_required, non_atomic, split_pending, or is_atomic=false
  in evidence_refs.claim_ids, linked_claim_ids, or supports_claim_ids.
- Do not let prior work or meta discourse dominate component cores.
- Set concepts (>= 2), prerequisite_concepts, introduced_concepts, and reused_concepts
  per component. Derivation components must include a mathematical/procedural concept,
  observable components an observable name, and comparison components a theory-class name.
- Return AT MOST 6 components. Prefer the headline-claim support components.
- Keep label <= 10 words, summary <= 40 words, reason <= 30 words,
  teaching_takeaway <= 30 words, and each review_note <= 20 words.
- Keep inputs/outputs/preconditions/cautions/internal_flow to the minimum needed
  for validation. Do not include long explanatory prose.

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
            "responsibility_type": "definition | model | observation_model | observable_basis | equation_system | derivation | constraint | application | limitation",
            "operation": "single main theoretical operation (define/linearize/eliminate/substitute/solve/derive/constrain/diagnose/forecast/compare/...)",
            "primary_operation": "string",
            "secondary_operations": [],
            "support_role": "theory_base | observable_bridge | derivation_core | result | application | limitation",
            "supports_claim_ids": [],
            "support_distance_to_headline_claim": 0,
            "support_kind": "direct | indirect | prerequisite | derivational | application | caveat",
            "concepts": [],
            "prerequisite_concepts": [],
            "introduced_concepts": [],
            "reused_concepts": [],
            "split_recommendation": {
                "required": False,
                "suggested_components": []
            },
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
            "constraints": [],
            "invalid_conditions": [],
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
        issue_payload = [_issue_payload(i, llm_input) for i in issues]
        issue_text = "\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        )
        content = (
            self._build_user_content(llm_input)
            + "\n\n## Previous Output\n"
            + json.dumps(previous_output, ensure_ascii=False, indent=2)
            + "\n\n## Validation Issues\n"
            + issue_text
            + "\n\n## Validation Issues JSON\n"
            + json.dumps(issue_payload, ensure_ascii=False, indent=2)
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
            "claim_centered_plan": llm_input.claim_centered_plan,
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
            "- Return AT MOST 3 components\n"
            "- Keep summaries/reasons concise; avoid long explanatory prose\n"
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


def _issue_payload(issue, llm_input: ComponentAssemblyLLMInput) -> dict:
    payload = {
        "code": getattr(issue, "rule_id", ""),
        "severity": getattr(issue, "severity", ""),
        "message": getattr(issue, "message", ""),
        "field": getattr(issue, "field", None),
    }
    invalid_value = _invalid_value_from_message(payload["message"])
    allowed_values = _allowed_values_for_issue(payload["code"], llm_input)
    if invalid_value:
        payload["invalid_value"] = invalid_value
    if allowed_values:
        payload["allowed_values"] = sorted(allowed_values)
    return payload


def _allowed_values_for_issue(rule_id: str, llm_input: ComponentAssemblyLLMInput) -> set[str]:
    if rule_id == "unresolved_claim_id":
        return _ids(llm_input.available_claims, "claim_id")
    if rule_id == "unresolved_evidence_id":
        return _ids(llm_input.available_evidence, "evidence_id")
    if rule_id == "unresolved_equation_id":
        return _ids(llm_input.available_equations, "equation_id")
    if rule_id == "unresolved_derivation_id":
        return {str(v) for v in llm_input.available_derivation_ids or [] if str(v)}
    if rule_id == "unresolved_dsl_node_id":
        return _ids(llm_input.available_dsl_nodes, "node_id")
    if rule_id == "unresolved_dsl_edge_id":
        return _ids(llm_input.available_dsl_edges, "edge_id")
    return set()


def _ids(items: list[dict], key: str) -> set[str]:
    return {str(item.get(key)) for item in items or [] if item.get(key)}


def _invalid_value_from_message(message: str) -> str | None:
    if "'" not in message:
        return None
    parts = message.split("'")
    return parts[1] if len(parts) >= 3 else None
