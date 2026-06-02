"""ComponentGraphAgent data models (Issue #266).

Builds logical dependency edges between assembled components using
a deterministic/LLM hybrid pipeline.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

GRAPH_SCHEMA_VERSION = "0.1.0"
COMPONENT_GRAPH_VERSION = "v1"

# Edge types understood by the downstream component graph schema.
# Must align with _GRAPH_RELATIONS in backend/api/routes/theory_components.py.
VALID_EDGE_TYPES = [
    "REQUIRES",
    "PRODUCES_FOR",
    "TRANSFORMS",
    "DEFINES",
    "ENABLES",
    "QUALIFIES",
    "CONFLICTS_WITH",
    "CHECKED_BY",
    "RELATED_TO",
    "INHIBITS",
    "DERIVES_FROM",
    # Theory-structure graph relations used by GraphNormalizer.
    "defines",
    "feeds_equation_system",
    "linearizes",
    "eliminates_bias",
    "derives",
    "constrains",
    "diagnoses",
    "requires_review",
]

SUPPORT_STATUSES = [
    "llm_inferred",
    "dependency_declared",
    "io_matched",
    "derivation_linked",
    "dsl_cross_edge",
]


@dataclass
class CartridgeContext:
    cartridge_id: str
    ontology: dict
    validation_rules: dict
    relation_types: dict
    aliases: dict | None = None
    notation_patterns: list | None = None


@dataclass
class ComponentGraphNode:
    component_id: str
    label: str
    component_type: str
    review_status: str = "teacher_review_required"
    display_order: int = 0
    origin: str = "paper"
    operation: str = ""
    theory_object: str = ""
    graph_layer: str = "main"
    maturity_source: str = ""
    publish_ready: bool = False
    input_equation_ids: list[str] = field(default_factory=list)
    intermediate_equation_ids: list[str] = field(default_factory=list)
    output_equation_ids: list[str] = field(default_factory=list)
    definition_equation_ids: list[str] = field(default_factory=list)
    constraint_equation_ids: list[str] = field(default_factory=list)
    review_required_equation_ids: list[str] = field(default_factory=list)
    eliminated_symbols: list[str] = field(default_factory=list)
    retained_symbols: list[str] = field(default_factory=list)
    derivation_operations: list[str] = field(default_factory=list)


@dataclass
class ComponentGraphEdge:
    edge_id: str
    source: str
    target: str
    edge_type: str
    support_status: str
    evidence_claims: list[str]
    reasoning: str
    confidence: float = 0.75
    evidence_equation_ids: list[str] = field(default_factory=list)
    review_status: str = "teacher_review_required"


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str
    message: str
    field: str | None = None


@dataclass
class ComponentGraphLLMInput:
    document_id: str
    cartridge_id: str | None
    # Material 1: component I/O and declared dependencies
    components: list[dict]
    # Material 2: cross-component DSL edges (micro relations)
    cross_component_dsl_edges: list[dict]
    # Material 3: derivation chains spanning multiple components
    multi_component_derivations: list[dict]
    # Material 4: evidence text for claim IDs referenced in materials 1–3
    claim_texts: dict[str, str]
    evidence_texts: dict[str, str]
    # Valid vocabulary for this call
    valid_edge_types: list[str]
    # Component ID index (for validation prompting)
    component_ids: list[str]


@dataclass
class ComponentGraphResult:
    document_id: str
    graph_schema_version: str
    cartridge_id: str | None
    nodes: list[ComponentGraphNode]
    edges: list[ComponentGraphEdge]
    review_notes: list[str]
    confidence: float
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_graph_payload(self) -> dict:
        """Return the component_graph.json-compatible payload."""
        return {
            "graph_schema_version": self.graph_schema_version,
            "document_id": self.document_id,
            "nodes": [
                {
                    "component_id": n.component_id,
                    "label": n.label,
                    "component_type": n.component_type,
                    "review_status": n.review_status,
                    "display_order": n.display_order,
                    "origin": n.origin,
                    "operation": n.operation,
                    "theory_object": n.theory_object,
                    "graph_layer": n.graph_layer,
                    "maturity_source": n.maturity_source,
                    "publish_ready": n.publish_ready,
                    "input_equation_ids": n.input_equation_ids,
                    "intermediate_equation_ids": n.intermediate_equation_ids,
                    "output_equation_ids": n.output_equation_ids,
                    "definition_equation_ids": n.definition_equation_ids,
                    "constraint_equation_ids": n.constraint_equation_ids,
                    "review_required_equation_ids": n.review_required_equation_ids,
                    "eliminated_symbols": n.eliminated_symbols,
                    "retained_symbols": n.retained_symbols,
                    "derivation_operations": n.derivation_operations,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "edge_id": e.edge_id,
                    "source_component_id": e.source,
                    "target_component_id": e.target,
                    "relation": e.edge_type,
                    "edge_type": e.edge_type,
                    "support_status": e.support_status,
                    "confidence": e.confidence,
                    "review_status": "teacher_review_required",
                    "evidence": {
                        "evidence_claims": e.evidence_claims,
                        "evidence_equation_ids": e.evidence_equation_ids,
                        "reason": e.reasoning,
                    },
                }
                for e in self.edges
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ComponentGraphResult":
        nodes = [ComponentGraphNode(**n) for n in d.get("nodes", [])]
        edges = [ComponentGraphEdge(**e) for e in d.get("edges", [])]
        issues = [ValidationIssue(**i) for i in d.get("validation_issues", [])]
        return cls(
            document_id=d["document_id"],
            graph_schema_version=d.get("graph_schema_version", GRAPH_SCHEMA_VERSION),
            cartridge_id=d.get("cartridge_id"),
            nodes=nodes,
            edges=edges,
            review_notes=list(d.get("review_notes", [])),
            confidence=float(d.get("confidence", 0.0)),
            validation_issues=issues,
        )

    @classmethod
    def make_fallback(
        cls,
        document_id: str,
        cartridge_id: str | None,
        reason: str,
        nodes: list[ComponentGraphNode] | None = None,
    ) -> "ComponentGraphResult":
        return cls(
            document_id=document_id,
            graph_schema_version=GRAPH_SCHEMA_VERSION,
            cartridge_id=cartridge_id,
            nodes=nodes or [],
            edges=[],
            review_notes=[f"ComponentGraphAgent failed: {reason}"],
            confidence=0.0,
            validation_issues=[ValidationIssue(
                rule_id="component_graph_failed",
                severity="error",
                message=reason,
                field="edges",
            )],
        )
