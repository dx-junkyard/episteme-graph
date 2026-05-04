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

ASSEMBLY_HINT_TYPES = [
    "candidate_bridge_component",
    "candidate_uncertainty_hub",
    "candidate_core_component",
    "candidate_correction_cluster",
    "candidate_diagnostic_cluster",
]


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

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "ComponentAssemblyResult":
        components = [ComponentRecord(**c) for c in d.get("components", [])]
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
