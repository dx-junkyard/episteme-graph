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
    # Issue #361: generic-operation step kept in the equation_detail layer
    # because its input/output equations still back it.
    "generic_operation",
    # Issue #358: two-layer linkage gaps recorded on the node itself.
    "orphan_detail_node",
    "empty_main_node",
]

# Edge types that are too generic to publish as confirmed theory structure.
GENERIC_EDGE_TYPES = {"related_to", "correlates", "supports", "transforms", "relate"}

# Operations that do not name a concrete theory operation. When a derivation
# step carries one of these, the resulting node/edge is treated as inferred and
# flagged for review instead of being published as confirmed structure.
GENERIC_OPERATIONS = {"transform", "relate", "connect", "support", "associate", ""}

# Graph layers (issue #306). The main graph carries aggregated, high-level
# theory-operation nodes; the equation_detail graph keeps one node per
# derivation step; the debug layer holds fallback / inferred nodes.
GRAPH_LAYER_MAIN = "main"
GRAPH_LAYER_EQUATION_DETAIL = "equation_detail"
GRAPH_LAYER_DEBUG = "debug"
GRAPH_LAYERS = [GRAPH_LAYER_MAIN, GRAPH_LAYER_EQUATION_DETAIL, GRAPH_LAYER_DEBUG]

# Component-type vocabulary for the two graph layers (issue #306).
THEORY_OPERATION_NODE = "TheoryOperationNode"  # aggregated, main graph
EQUATION_OPERATION_NODE = "EquationOperationNode"  # per-step, detail graph

# Theory stages (issue #308). The main graph aggregates equation-level steps into
# a handful of high-level theory-construction stages so it reads as a backbone
# (5–8 nodes) rather than one node per equation/operation. The vocabulary is
# domain-neutral; a stage is assigned from an operation's *edge_type family*
# (see classify_operation), never from paper- or field-specific terms.
THEORY_STAGE_BASIS = "theory_basis"
THEORY_STAGE_OBSERVATION_MODEL = "observation_model"
THEORY_STAGE_OBSERVABLE_CONSTRUCTION = "observable_construction"
THEORY_STAGE_EQUATION_SYSTEM = "equation_system"
THEORY_STAGE_ELIMINATION = "elimination"
THEORY_STAGE_CONSISTENCY_RELATION = "consistency_relation"
THEORY_STAGE_DIAGNOSTIC_APPLICATION = "diagnostic_application"

# Canonical ordering used for the main graph's top-to-bottom theory flow.
THEORY_STAGES = [
    THEORY_STAGE_BASIS,
    THEORY_STAGE_OBSERVATION_MODEL,
    THEORY_STAGE_OBSERVABLE_CONSTRUCTION,
    THEORY_STAGE_EQUATION_SYSTEM,
    THEORY_STAGE_ELIMINATION,
    THEORY_STAGE_CONSISTENCY_RELATION,
    THEORY_STAGE_DIAGNOSTIC_APPLICATION,
]

# Human-readable, UI-facing stage labels (issue #308). Used as the *base* label
# of a main TheoryOperationNode; an atomic-claim / reason phrase may enrich it but
# the equation-id fallback is never used for a main label.
THEORY_STAGE_LABELS = {
    THEORY_STAGE_BASIS: "Theory basis",
    THEORY_STAGE_OBSERVATION_MODEL: "Observation model",
    THEORY_STAGE_OBSERVABLE_CONSTRUCTION: "Observable construction",
    THEORY_STAGE_EQUATION_SYSTEM: "Equation system",
    THEORY_STAGE_ELIMINATION: "Elimination",
    THEORY_STAGE_CONSISTENCY_RELATION: "Consistency relation",
    THEORY_STAGE_DIAGNOSTIC_APPLICATION: "Diagnostic / application",
}

# Domain-neutral mapping from an operation edge_type family to a theory stage.
# Every non-generic edge_type produced by classify_operation is covered so a main
# node always has a stage (hence a non-equation label).
_EDGE_TYPE_TO_STAGE = {
    "defines": THEORY_STAGE_BASIS,
    "constructs": THEORY_STAGE_OBSERVABLE_CONSTRUCTION,
    "normalizes": THEORY_STAGE_OBSERVABLE_CONSTRUCTION,
    "linearizes": THEORY_STAGE_EQUATION_SYSTEM,
    "approximates": THEORY_STAGE_EQUATION_SYSTEM,
    "substitutes": THEORY_STAGE_EQUATION_SYSTEM,
    "solves": THEORY_STAGE_ELIMINATION,
    "eliminates": THEORY_STAGE_ELIMINATION,
    "derives": THEORY_STAGE_CONSISTENCY_RELATION,
    "constrains": THEORY_STAGE_CONSISTENCY_RELATION,
    "diagnoses": THEORY_STAGE_DIAGNOSTIC_APPLICATION,
    "compares": THEORY_STAGE_DIAGNOSTIC_APPLICATION,
}


def stage_for_edge_type(edge_type: str) -> str:
    """Map an operation edge_type family to a theory stage (domain-neutral).

    Returns ``""`` for generic / unmapped edge types; callers keep such steps in
    the equation_detail / debug layer rather than promoting them to a main node.
    """
    return _EDGE_TYPE_TO_STAGE.get(str(edge_type or "").strip().lower(), "")


def theory_stage_label(stage: str) -> str:
    """Human-readable label for a theory stage (falls back to a humanized form)."""
    key = str(stage or "").strip().lower()
    if key in THEORY_STAGE_LABELS:
        return THEORY_STAGE_LABELS[key]
    return key.replace("_", " ").capitalize() if key else "Theory stage"


# Lower-cased canonical stage label → canonical display label, used to recognise
# a main node label that already starts with a stage name (issue #319).
_STAGE_LABEL_LOOKUP = {label.lower(): label for label in THEORY_STAGE_LABELS.values()}

# Domain-neutral keyword → stage label for legacy / long main labels that do not
# already begin with a canonical stage name (issue #319). Keyed by the operation
# family words classify_operation derives stages from — never paper-specific
# terms. Order matters: more specific multi-word phrases come first.
_STAGE_KEYWORD_LABELS: list[tuple[tuple[str, ...], str]] = [
    (("theory basis", "theory_basis", "definition", "define", "basis"), "Theory basis"),
    (("observation model", "observation_model"), "Observation model"),
    (
        ("observable construction", "observable_construction", "construct", "normalize"),
        "Observable construction",
    ),
    (
        ("equation system", "equation_system", "linearize", "solve", "approximate", "substitute", "system"),
        "Equation system",
    ),
    (("elimination", "eliminate"), "Elimination"),
    (
        ("consistency relation", "consistency_relation", "constraint", "constrain", "derive", "diagnose"),
        "Consistency relation",
    ),
    (("diagnostic", "application", "compare", "test"), "Diagnostic / application"),
]


def split_main_label(label: str) -> tuple[str, str]:
    """Split a (possibly long) main TheoryOperationNode label (issue #319).

    Returns ``(short_label, description_remainder)`` where ``short_label`` is a
    canonical, short theory-stage name (never an equation id, claim sentence or
    reason sentence) and ``description_remainder`` is the longer explanatory text
    that should be preserved in the node's ``description`` instead of its label.

    Behaviour:
      * ``"Theory basis: Eq. (2.7) ..."`` -> ``("Theory basis", "Eq. (2.7) ...")``
        (a label that already starts with a canonical stage name followed by
        ``:`` is split at the first colon).
      * A bare canonical stage name -> ``(stage, "")`` (no description added).
      * Otherwise the stage is inferred from domain-neutral keywords and the full
        original text is kept as the description remainder so nothing is lost.
      * An unmappable label is returned unchanged with an empty remainder.

    This helper is shared by the stored-graph normaliser
    (``_normalize_stored_component_graph``) so the API response, and by extension
    the UI, always show short main labels.
    """
    text = str(label or "").strip()
    if not text:
        return "", ""
    # "<Stage>: <long description>" -> split at the first colon.
    if ":" in text:
        head, _, tail = text.partition(":")
        head_key = head.strip().lower()
        if head_key in _STAGE_LABEL_LOOKUP:
            return _STAGE_LABEL_LOOKUP[head_key], tail.strip()
    # Already a bare canonical stage label.
    if text.lower() in _STAGE_LABEL_LOOKUP:
        return _STAGE_LABEL_LOOKUP[text.lower()], ""
    # Infer the stage from domain-neutral keywords; keep the full text as the
    # description remainder so the long explanation is preserved.
    lowered = text.lower()
    for keywords, stage_label in _STAGE_KEYWORD_LABELS:
        if any(kw in lowered for kw in keywords):
            return stage_label, text
    # Unmappable: leave the label untouched and add no description remainder.
    return text, ""

# Map a node's source-backing status to a review_status (issue #306). A
# source-backed node should not stay ``teacher_review_required`` by default;
# inferred / review_required nodes must surface as review_required.
REVIEW_STATUS_BY_BACKING = {
    "source_backed": "source_backed",
    "partially_source_backed": "teacher_review_required",
    "inferred": "review_required",
    "review_required": "review_required",
}


def review_status_for_backing(source_backing_status: str) -> str:
    """Return the review_status implied by a node's source-backing status."""
    key = str(source_backing_status or "").strip().lower()
    return REVIEW_STATUS_BY_BACKING.get(key, "review_required")

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
    display_label: str = ""
    representative_component_id: str = ""
    linked_component_ids: list[str] = field(default_factory=list)
    detail_node_ids: list[str] = field(default_factory=list)
    supporting_derivation_ids: list[str] = field(default_factory=list)
    # Longer, human-readable explanation (atomic-claim text / step reason). The
    # ``label`` stays short (a theory-stage name for main nodes); the descriptive
    # sentence lives here and is shown in the UI's detail pane (issue #308).
    description: str = ""
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
    # Layer linkage (issue #306). A main TheoryOperationNode lists the
    # equation_detail nodes it aggregates in ``member_component_ids``; each
    # equation_detail node points back at its main node via ``parent_component_id``.
    parent_component_id: str = ""
    member_component_ids: list[str] = field(default_factory=list)
    support_role: str = ""
    supports_claim_ids: list[str] = field(default_factory=list)
    support_distance_to_headline_claim: int = 0
    support_kind: str = ""
    # Concept tags carried from component assembly (issue #8).
    concepts: list[str] = field(default_factory=list)
    prerequisite_concepts: list[str] = field(default_factory=list)
    # Short graph-display label, guaranteed ≤ 30 chars (issue #337). The primary
    # label shown on graph nodes. For main nodes: theory-stage name; for detail
    # nodes: operation verb. Frontend translates to Japanese.
    visual_label: str = ""
    # Claim I/O (issue #337): separate input/output claims carried from the
    # derivation step so the right panel can display them.
    input_claim_ids: list[str] = field(default_factory=list)
    output_claim_ids: list[str] = field(default_factory=list)
    # Precondition claims required (but not consumed) by this step (issue #337).
    # Distinct from ``input_claim_ids`` (data-flow inputs).
    required_claim_ids: list[str] = field(default_factory=list)
    # Extraction/review note (issue #337). Free-text step.reason from the
    # derivation pipeline; distinct from ``review_reasons`` (structured codes)
    # and ``description`` (reader-facing explanation).
    review_reason: str = ""
    # Issue #449: true when this node is a thesis traversal anchor (the goal of
    # the argument). Mirrors DSLNode.is_thesis_anchor; carried through to the UI
    # so anchors are emphasised via the flag, not ID string matching (#443).
    is_thesis_anchor: bool = False


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
    # source_backing_status mirrors the node field so node *and* edge expose the
    # same backing vocabulary (issue #311 criterion 6): "source_backed" |
    # "partially_source_backed" | "review_required" | "inferred". "" = unclassified.
    source_backing_status: str = ""
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
                    "display_label": n.display_label,
                    "representative_component_id": n.representative_component_id,
                    "linked_component_ids": n.linked_component_ids,
                    "detail_node_ids": n.detail_node_ids,
                    "supporting_derivation_ids": n.supporting_derivation_ids,
                    "description": n.description,
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
                    "parent_component_id": n.parent_component_id,
                    "member_component_ids": n.member_component_ids,
                    "visual_label": n.visual_label,
                    "input_claim_ids": n.input_claim_ids,
                    "output_claim_ids": n.output_claim_ids,
                    "required_claim_ids": n.required_claim_ids,
                    "review_reason": n.review_reason,
                    "is_thesis_anchor": n.is_thesis_anchor,
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
                    "source_backing_status": e.source_backing_status,
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
