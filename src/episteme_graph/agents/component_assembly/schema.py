"""ComponentAssemblyAgent data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

COMPONENTS_VERSION = "v1"

CORE_COMPONENT_TYPES = [
    "RelationComponent",
    "AssumptionComponent",
    "CorrectionComponent",
    "UncertaintyComponent",
    "DiagnosticComponent",
    "MethodComponent",
    "TheoryComponent",
    "ClaimBundleComponent",
]

CORE_DEPENDENCY_TYPES = [
    "requires",
    "depends_on",
    "refines",
    "qualifies",
    "supports",
    "diagnoses",
    "propagates_uncertainty_to",
]

DEPENDENCY_TYPE_ALIASES = {
    # LLMs often emit the past-participle wording when describing a relation
    # that qualifies another component. The schema uses the verb form.
    "qualified": "qualifies",
}

ASSEMBLY_HINT_TYPES = [
    "candidate_bridge_component",
    "candidate_uncertainty_hub",
    "candidate_core_component",
    "candidate_correction_cluster",
    "candidate_diagnostic_cluster",
]


def normalize_dependency_type(value: object) -> str:
    raw = str(value or "").strip().lower()
    return DEPENDENCY_TYPE_ALIASES.get(raw, raw)


def normalize_dependencies(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    dependencies: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        dep = dict(item)
        dep["dependency_type"] = normalize_dependency_type(dep.get("dependency_type"))
        dep["component_refs"] = list(dep.get("component_refs") or [])
        dep["reason"] = str(dep.get("reason", ""))
        dependencies.append(dep)
    return dependencies


@dataclass
class CartridgeContext:
    cartridge_id: str
    ontology: dict
    component_types: dict
    relation_types: dict
    validation_rules: dict
    aliases: dict | None = None
    notation_patterns: list | None = None
    normalization_rules: list | None = None


@dataclass
class ComponentAssemblyLLMInput:
    document_id: str
    cartridge_id: str | None
    accepted_claims: list[dict]
    equations: list[dict]
    thesis_nodes: list[dict]
    dsl_nodes: list[dict]
    dsl_edges: list[dict]
    allowed_component_types: list[str]
    allowed_dependency_types: list[str]
    normalized_terms: list[dict] | None = None
    # Deterministic artifact ID lists for cross-reference validation
    available_claims: list[dict] = field(default_factory=list)
    available_evidence: list[dict] = field(default_factory=list)
    available_equations: list[dict] = field(default_factory=list)
    available_dsl_nodes: list[dict] = field(default_factory=list)
    available_dsl_edges: list[dict] = field(default_factory=list)
    available_derivation_ids: list[str] = field(default_factory=list)


@dataclass
class ComponentFieldRef:
    text: str
    node_refs: list[str] | None = None
    claim_ids: list[str] | None = None
    equation_ids: list[str] | None = None


@dataclass
class ComponentDependency:
    dependency_type: str
    component_refs: list[str]
    reason: str


INTERNAL_FLOW_REQUIRED_TYPES = {
    "RelationComponent",
    "PaperRelationComponent",
    "CorrectionComponent",
    "DiagnosticComponent",
    "MethodComponent",
}


@dataclass
class ComponentRecord:
    component_id: str
    component_type: str
    label: str
    summary: str
    inputs: list[dict]
    outputs: list[dict]
    preconditions: list[dict]
    cautions: list[dict]
    dependencies: list[dict]
    evidence_refs: dict
    reason: str
    confidence: float
    review_notes: list[str]
    internal_flow: list[dict] = field(default_factory=list)
    # Typed linked IDs (issue #262)
    linked_claim_ids: list[str] = field(default_factory=list)
    linked_equation_ids: list[str] = field(default_factory=list)
    linked_evidence_ids: list[str] = field(default_factory=list)
    linked_derivation_ids: list[str] = field(default_factory=list)
    linked_dsl_node_ids: list[str] = field(default_factory=list)
    linked_dsl_edge_ids: list[str] = field(default_factory=list)
    input_equation_ids: list[str] = field(default_factory=list)
    intermediate_equation_ids: list[str] = field(default_factory=list)
    output_equation_ids: list[str] = field(default_factory=list)
    constraint_equation_ids: list[str] = field(default_factory=list)
    definition_equation_ids: list[str] = field(default_factory=list)
    review_required_equation_ids: list[str] = field(default_factory=list)
    eliminated_symbols: list[str] = field(default_factory=list)
    retained_symbols: list[str] = field(default_factory=list)
    equation_confidence_summary: dict = field(default_factory=dict)
    review_status: str = "teacher_review_required"
    teaching_takeaway: str = ""
    source_scope: dict = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    approximations: list[str] = field(default_factory=list)
    # Single main theoretical operation for this component (issue #300).
    # Populated by ComponentRefiner; one of the theory-operation families
    # (define, linearize, eliminate, substitute, solve, derive, constrain, ...).
    operation: str = ""


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class ComponentAssemblyResult:
    document_id: str
    components_version: str
    cartridge_id: str | None
    components: list[ComponentRecord]
    assembly_hints: list[dict]
    review_notes: list[str]
    confidence: float
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    # ComponentRefiner output: record of summary→theory-operation splits (issue #300).
    refinement_report: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "ComponentAssemblyResult":
        components = []
        for c in d.get("components", []):
            components.append(ComponentRecord(
                component_id=c.get("component_id", ""),
                component_type=c.get("component_type", ""),
                label=c.get("label", ""),
                summary=c.get("summary", ""),
                inputs=list(c.get("inputs") or []),
                outputs=list(c.get("outputs") or []),
                preconditions=list(c.get("preconditions") or []),
                cautions=list(c.get("cautions") or []),
                dependencies=normalize_dependencies(c.get("dependencies")),
                evidence_refs=c.get("evidence_refs") or {},
                reason=c.get("reason", ""),
                confidence=float(c.get("confidence", 0.0)),
                review_notes=list(c.get("review_notes") or []),
                internal_flow=list(c.get("internal_flow") or []),
                linked_claim_ids=list(c.get("linked_claim_ids") or []),
                linked_equation_ids=list(c.get("linked_equation_ids") or []),
                linked_evidence_ids=list(c.get("linked_evidence_ids") or []),
                linked_derivation_ids=list(c.get("linked_derivation_ids") or []),
                linked_dsl_node_ids=list(c.get("linked_dsl_node_ids") or []),
                linked_dsl_edge_ids=list(c.get("linked_dsl_edge_ids") or []),
                input_equation_ids=list(c.get("input_equation_ids") or []),
                intermediate_equation_ids=list(c.get("intermediate_equation_ids") or []),
                output_equation_ids=list(c.get("output_equation_ids") or []),
                constraint_equation_ids=list(c.get("constraint_equation_ids") or []),
                definition_equation_ids=list(c.get("definition_equation_ids") or []),
                review_required_equation_ids=list(c.get("review_required_equation_ids") or []),
                eliminated_symbols=list(c.get("eliminated_symbols") or []),
                retained_symbols=list(c.get("retained_symbols") or []),
                equation_confidence_summary=c.get("equation_confidence_summary") or {},
                review_status=c.get("review_status", "teacher_review_required"),
                teaching_takeaway=c.get("teaching_takeaway", ""),
                source_scope=c.get("source_scope") or {},
                assumptions=list(c.get("assumptions") or []),
                approximations=list(c.get("approximations") or []),
                operation=c.get("operation", ""),
            ))
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            components_version=d.get("components_version", COMPONENTS_VERSION),
            cartridge_id=d.get("cartridge_id"),
            components=components,
            assembly_hints=d.get("assembly_hints", []),
            review_notes=d.get("review_notes", []),
            confidence=float(d.get("confidence", 0.0)),
            validation_issues=issues,
            refinement_report=d.get("refinement_report") or {},
        )

    @classmethod
    def make_fallback(
        cls,
        document_id: str,
        cartridge_id: str | None,
        reason: str,
    ) -> "ComponentAssemblyResult":
        return cls(
            document_id=document_id,
            components_version=COMPONENTS_VERSION,
            cartridge_id=cartridge_id,
            components=[],
            assembly_hints=[],
            review_notes=[f"Component assembly failed: {reason}"],
            confidence=0.0,
            validation_issues=[ValidationIssue(
                rule_id="component_assembly_failed",
                severity="error",
                message=reason,
                field="components",
            )],
        )
