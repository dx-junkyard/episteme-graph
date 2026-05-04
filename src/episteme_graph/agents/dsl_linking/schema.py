"""DSLLinkingAgent data models."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

DSL_VERSION = "v1"

NODE_TYPES = [
    "TheoreticalFramework",
    "Approximation",
    "Relation",
    "EquationRelation",
    "Observable",
    "Parameter",
    "UncertaintySource",
    "CorrectionSource",
    "CorrectionTerm",
    "Method",
    "Experiment",
    "Constraint",
    "Diagnostic",
    "Result",
    "ClaimProxy",
    "ThesisProxy",
]

CONCRETE_NODE_TYPES = {
    "EquationRelation",
    "Observable",
    "Approximation",
    "TheoreticalFramework",
    "CorrectionSource",
    "CorrectionTerm",
    "UncertaintySource",
    "Method",
    "Experiment",
    "Result",
    "Diagnostic",
    "Parameter",
    "Constraint",
    "Relation",
}

PROXY_NODE_TYPES = {"ClaimProxy", "ThesisProxy"}

SOURCE_KINDS = ["claim", "equation", "thesis", "mixed"]

CORE_PREDICATES = [
    "REQUIRES",
    "PRODUCES",
    "TRANSFORMS",
    "DEFINES",
    "MEASURES",
    "CONTAINS",
    "CORRELATES",
    "CAUSES",
    "INHIBITS",
    "EQUIVALENT",
]

DOMAIN_VERBS = [
    "assumes",
    "derives",
    "defines",
    "relates",
    "normalizes",
    "corrects",
    "qualifies",
    "checks_consistency_of",
    "contributes_to_uncertainty",
    "contributes_to_correction",
    "takes_specific_limit",
    "integrates_to",
    "motivates",
    "depends_on",
    "supports",
    "summarizes",
    "enables_derivation",
]

PREDICATE_PREFERRED_TARGETS = {
    "TRANSFORMS": {"EquationRelation", "Relation", "Result"},
    "REQUIRES": {"Approximation", "TheoreticalFramework", "Constraint", "Parameter"},
    "DEFINES": {"Observable", "Parameter", "EquationRelation"},
    "CAUSES": {"CorrectionTerm", "CorrectionSource", "Result"},
    "CORRELATES": {"UncertaintySource", "Result", "Parameter"},
    "MEASURES": {"Observable", "Diagnostic", "Result"},
    "INHIBITS": {"UncertaintySource", "CorrectionTerm", "Result"},
}

PREDICATE_PREFERRED_SOURCES = {
    "TRANSFORMS": {"EquationRelation", "Relation", "Method"},
    "CAUSES": {"CorrectionSource", "Approximation", "TheoreticalFramework", "Parameter"},
    "MEASURES": {"Diagnostic", "Experiment", "Method", "Observable"},
}

POLARITIES = ["+", "-", "+/-", "?"]

GRAPH_HINT_TYPES = [
    "component_seed",
    "high_centrality_node",
    "candidate_bridge",
    "candidate_uncertainty_cluster",
    "candidate_correction_cluster",
]


@dataclass
class DSLLLMInput:
    document_id: str
    accepted_claims: list[dict]
    equations: list[dict]
    thesis_nodes: list[dict]
    excluded_from_core: list[dict]


@dataclass
class DSLNode:
    node_id: str
    node_type: str
    node_value: str
    source_kind: str
    source_refs: dict
    reason: str
    confidence: float


@dataclass
class DSLEdge:
    edge_id: str
    from_node_id: str
    to_node_id: str
    core_predicate: str
    domain_verb: str
    polarity: str
    evidence_refs: dict
    reason: str
    confidence: float


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class DSLLinkingResult:
    document_id: str
    dsl_version: str
    nodes: list[DSLNode]
    edges: list[DSLEdge]
    graph_hints: list[dict]
    review_notes: list[str]
    confidence: float
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "DSLLinkingResult":
        nodes = [DSLNode(**n) for n in d.get("nodes", [])]
        edges = [DSLEdge(**e) for e in d.get("edges", [])]
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            dsl_version=d.get("dsl_version", DSL_VERSION),
            nodes=nodes,
            edges=edges,
            graph_hints=d.get("graph_hints", []),
            review_notes=d.get("review_notes", []),
            confidence=float(d.get("confidence", 0.0)),
            validation_issues=issues,
        )

    @classmethod
    def make_fallback(cls, document_id: str, reason: str) -> "DSLLinkingResult":
        return cls(
            document_id=document_id,
            dsl_version=DSL_VERSION,
            nodes=[],
            edges=[],
            graph_hints=[],
            review_notes=[f"DSL linking failed: {reason}"],
            confidence=0.0,
            validation_issues=[ValidationIssue(
                rule_id="dsl_linking_failed",
                severity="error",
                message=reason,
                field="nodes",
            )],
        )
