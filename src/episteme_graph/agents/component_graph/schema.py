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
    # Domain-neutral operation-derived edge types (issue #302).
    "defines",
    "constructs",
    "linearizes",
    "solves",
    "substitutes",
    "eliminates",
    "derives",
    "constrains",
    "diagnoses",
    "compares",
    "normalizes",
    "approximates",
    "transforms",
    "feeds",
    "requires_review",
    # Retained for backward compatibility with previously stored graphs.
    "feeds_equation_system",
    "eliminates_bias",
]

# Source-backing classification for theory-operation nodes (issue #302).
SOURCE_BACKING_STATUSES = [
    "source_backed",
    "partially_source_backed",
    "inferred",
    "review_required",
]

# Structured review-reason vocabulary (issue #302). review_required nodes/edges
# must always carry at least one of these so the UI can explain *why*.
REVIEW_REASONS = [
    "missing_atomic_claim",
    "missing_evidence_link",
    "missing_equation_link",
    "missing_derivation_link",
    "equation_needs_math_review",
    "edge_not_source_backed",
    "fallback_or_inferred_node",
    "source_span_missing",
]

# Edge types that are too generic to publish as confirmed theory structure.
GENERIC_EDGE_TYPES = {"related_to", "correlates", "supports", "transforms", "relate"}

# Operations that do not name a concrete theory operation. When a derivation
# step carries one of these, the resulting node/edge is treated as inferred and
# flagged for review instead of being published as confirmed structure.
GENERIC_OPERATIONS = {"transform", "relate", "connect", "support", "associate", ""}

# Full operation name → (verb, edge_type) for ontology operations that do not
# follow the simple ``<prefix>_<object>`` shape.
_OPERATION_FULL_MAP = {
    "apply_definition": ("Apply definition", "defines"),
    "apply_criterion": ("Apply criterion", "diagnoses"),
    "apply_constraint": ("Apply constraint", "constrains"),
    "apply_equation": ("Apply equation", "transforms"),
    "apply_measurement_or_update": ("Apply measurement", "transforms"),
    "apply_non_disturbance_or_independence": ("Apply independence", "constrains"),
    "introduce_observable": ("Introduce observable", "defines"),
    "solve_linear_system": ("Solve linear system", "solves"),
    "eliminate_parameter": ("Eliminate parameter", "eliminates"),
    "derive_result": ("Derive result", "derives"),
    "infer_conclusion": ("Infer conclusion", "derives"),
    "infer_intermediate_claim": ("Infer intermediate claim", "derives"),
    "state_assumption": ("State assumption", "defines"),
    "branch_on_condition": ("Branch on condition", "constrains"),
    "flag_limitation": ("Flag limitation", "constrains"),
}

# First token of an operation → (verb, edge_type). Domain-neutral; the object
# part of the operation name is appended to keep the label specific.
_OPERATION_PREFIX_MAP = {
    "define": ("Define", "defines"),
    "construct": ("Construct", "constructs"),
    "linearize": ("Linearize", "linearizes"),
    "solve": ("Solve", "solves"),
    "substitute": ("Substitute", "substitutes"),
    "eliminate": ("Eliminate", "eliminates"),
    "derive": ("Derive", "derives"),
    "constrain": ("Constrain", "constrains"),
    "diagnose": ("Diagnose", "diagnoses"),
    "compare": ("Compare", "compares"),
    "normalize": ("Normalize", "normalizes"),
    "approximate": ("Approximate", "approximates"),
    "introduce": ("Introduce", "defines"),
    "state": ("State", "defines"),
    "infer": ("Infer", "derives"),
    "flag": ("Flag", "constrains"),
}


def classify_operation(operation: str) -> tuple[str, str, bool]:
    """Map a derivation operation to a (verb, edge_type, is_generic) triple.

    Domain-neutral: no paper- or field-specific terms. ``is_generic`` is True
    when the operation does not name a concrete theory operation, in which case
    the resulting node/edge should be flagged for review rather than published.
    """
    op = str(operation or "").strip().lower()
    if op in GENERIC_OPERATIONS:
        verb = op.replace("_", " ").capitalize() if op else "Operation"
        return verb, "requires_review", True
    if op in _OPERATION_FULL_MAP:
        verb, edge_type = _OPERATION_FULL_MAP[op]
        return verb, edge_type, False
    prefix = op.split("_", 1)[0]
    if prefix in _OPERATION_PREFIX_MAP:
        verb, edge_type = _OPERATION_PREFIX_MAP[prefix]
        rest = op.split("_")[1:]
        label = (verb + " " + " ".join(rest)).strip() if rest else verb
        return label, edge_type, False
    # Unknown operation: keep it but treat as a generic transform pending review.
    return op.replace("_", " ").capitalize(), "transforms", True


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
    # Source-backing links and status (issue #302).
    linked_equation_ids: list[str] = field(default_factory=list)
    linked_derivation_ids: list[str] = field(default_factory=list)
    linked_claim_ids: list[str] = field(default_factory=list)
    linked_evidence_ids: list[str] = field(default_factory=list)
    # Set explicitly by the normalizer; "" means not yet classified.
    source_backing_status: str = ""
    review_reasons: list[str] = field(default_factory=list)


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
    # review_status here records evidence backing (issue #302):
    # "source_backed" | "review_required". "" means not yet classified.
    review_status: str = ""
    # Source-backing links (issue #302).
    evidence_derivation_ids: list[str] = field(default_factory=list)
    evidence_claim_ids: list[str] = field(default_factory=list)
    source_evidence_ids: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)


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
                    "linked_equation_ids": n.linked_equation_ids,
                    "linked_derivation_ids": n.linked_derivation_ids,
                    "linked_claim_ids": n.linked_claim_ids,
                    "linked_evidence_ids": n.linked_evidence_ids,
                    "source_backing_status": n.source_backing_status,
                    "review_reasons": n.review_reasons,
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
                    "review_status": e.review_status,
                    "review_reasons": e.review_reasons,
                    "evidence": {
                        "evidence_claims": e.evidence_claims,
                        "evidence_equation_ids": e.evidence_equation_ids,
                        "evidence_derivation_ids": e.evidence_derivation_ids,
                        "evidence_claim_ids": e.evidence_claim_ids,
                        "source_evidence_ids": e.source_evidence_ids,
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
