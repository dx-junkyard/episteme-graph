"""Issue #8 end-to-end concept wiring (non-LLM).

These tests exercise the *connections* between stages rather than LLM output
quality: a claim's concepts flow into components, components roll up into the
component graph and course mapping, and the export validation gate emits a
``concept_validation`` block. Four scenarios are covered: a clean happy path, a
low-confidence path, a composite-claim path, and a missing-concepts path.
"""
from __future__ import annotations

import importlib.util
import os
import sys

from episteme_graph.agents.claim_object_builder import ClaimObjectBuilder
from episteme_graph.agents.claim_qualification.schema import QualifiedSpanRecord
from episteme_graph.agents.component_assembly.enrichment import enrich_component_assembly
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyLLMInput,
    ComponentAssemblyResult,
    ComponentRecord,
)
from episteme_graph.agents.component_graph.normalizer import ComponentGraphNormalizer
from episteme_graph.agents.component_graph.schema import (
    GRAPH_SCHEMA_VERSION,
    ComponentGraphResult,
)
from episteme_graph.agents.course_mapping import CourseMappingAgent
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)
from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    TypedBlock,
)
from episteme_graph.agents.evidence_registry import EvidenceRegistryBuilder

# Import the export gate the same way test_export_validation_gate does, to avoid
# pulling sqlalchemy in via the package __init__.
_GATE_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../../backend/core/document_pipeline/export_validation_gate.py",
)
_spec = importlib.util.spec_from_file_location("export_validation_gate_int", _GATE_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["export_validation_gate_int"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
ExportValidationGate = _mod.ExportValidationGate

_ONTOLOGY = {
    "aliases": {
        "Skewness": ["skewness"],
        "Kurtosis": ["kurtosis"],
        "Galaxy bias": ["galaxy bias", "nonlinear galaxy bias"],
    },
    "concept_types": {
        "Skewness": "observable",
        "Kurtosis": "observable",
        "Galaxy bias": "nuisance",
    },
}

_CONCEPT_WARNINGS = {
    "MAIN_CLAIM_INSUFFICIENT_CONCEPTS",
    "MAIN_COMPONENT_INSUFFICIENT_CONCEPTS",
    "COMPONENT_CONCEPT_ROLE_MISMATCH",
}


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _structure(block_id: str, text: str) -> DocumentStructureResult:
    return DocumentStructureResult(
        document_id="doc",
        source_file="/tmp/x.pdf",
        cartridge_id="particle_physics",
        metadata=DocumentMetadata(pages=10),
        sections=[],
        blocks=[
            TypedBlock(
                block_id=block_id,
                page=4,
                order=1,
                text=text,
                block_type="body_paragraph",
                section_id="doc:sec_1",
            ),
        ],
    )


def _registry(block_id: str, text: str):
    builder = EvidenceRegistryBuilder(_structure(block_id, text))
    builder.add_for_block(block_id)
    return builder.build("doc", "particle_physics")


def _span(text: str, *, claim_type: str = "result", atomic_claims=None) -> QualifiedSpanRecord:
    span = QualifiedSpanRecord(
        span_id="span_001",
        block_id="blk_1",
        section_id="doc:sec_1",
        text=text,
        role_labels=[claim_type],
        qualification={
            "status": "accepted",
            "claim_type_candidate": claim_type,
            "claim_tier": "paper_core",
            "granularity": "good",
            "evidence_adequacy": "sufficient",
        },
        edit_suggestions={"normalized_text": text},
        reason="auto",
        confidence=0.82,
    )
    if atomic_claims is not None:
        span.atomic_claims = atomic_claims
    return span


def _build_claims(text: str, *, claim_type: str = "result", ontology=None, atomic_claims=None):
    registry = _registry("blk_1", text)
    builder = ClaimObjectBuilder(evidence_registry=registry, cartridge_ontology=ontology)
    return builder.build("doc", [_span(text, claim_type=claim_type, atomic_claims=atomic_claims)])


def _available_claims(build_result):
    """Translate built claim objects into the dict rows enrichment consumes."""
    return [
        {
            "claim_id": c.claim_id,
            "is_atomic": c.is_atomic,
            "atomicity": c.atomicity,
            "concepts": [cc.normalized for cc in c.concepts],
            "concept_assignment_status": c.concept_assignment_status,
        }
        for c in build_result.claims
    ]


def _component(component_id, *, linked_claim_ids, operation="eliminate", linked_equation_ids=None):
    return ComponentRecord(
        component_id=component_id,
        component_type="RelationComponent",
        label="Eliminate bias parameters",
        summary="Eliminates nuisance bias parameters.",
        inputs=[{"name": "equation system"}],
        outputs=[{"name": "bias-free relation"}],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={"equation_ids": list(linked_equation_ids or ["eq_c", "eq_d"])},
        reason="component",
        confidence=0.8,
        review_notes=[],
        linked_claim_ids=list(linked_claim_ids),
        supports_claim_ids=list(linked_claim_ids),
        linked_equation_ids=list(linked_equation_ids or ["eq_c", "eq_d"]),
        linked_derivation_ids=["deriv"],
        operation=operation,
    )


def _enrich(components, available_claims, equations=None):
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, list(components), [], [], 0.8)
    llm_input = ComponentAssemblyLLMInput(
        document_id="doc",
        cartridge_id=None,
        accepted_claims=[],
        equations=equations or [],
        thesis_nodes=[],
        dsl_nodes=[],
        dsl_edges=[],
        allowed_component_types=["RelationComponent"],
        allowed_dependency_types=["requires"],
        available_claims=available_claims,
    )
    enrich_component_assembly(result, llm_input)
    return result


def _derivations():
    steps = [
        DerivationStep(step_id="s1", input_equation_ids=["eq_b"], operation="define", output_equation_ids=["eq_c"]),
        DerivationStep(
            step_id="s2",
            input_equation_ids=["eq_c"],
            operation="eliminate_bias",
            output_equation_ids=["eq_d"],
            required_claim_ids=["claim_span_001"],
        ),
    ]
    return DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[DerivationChainRecord(
            derivation_id="deriv", document_id="doc", source_section_ids=[], steps=steps,
        )],
    )


def _empty_graph():
    return ComponentGraphResult(
        document_id="doc",
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None,
        nodes=[],
        edges=[],
        review_notes=[],
        confidence=0.0,
    )


def _graph(components):
    return ComponentGraphNormalizer().normalize(_empty_graph(), components, _derivations())


def _course(components):
    return CourseMappingAgent().run("doc", components.components)


def _export(build_result, components, course):
    artifacts = {
        "document_structure": {"blocks": [], "sections": []},
        "source_chunking": [{"text": "chunk"}],
        "claim_qualification": {"qualified_spans": []},
        "component_assembly": {"components": [], "validation_issues": []},
    }
    return ExportValidationGate().run(
        artifacts=artifacts,
        component_result=components,
        course_mapping=course,
        claim_objects=build_result,
    )


# ---------------------------------------------------------------------------
# Scenario 1: minimal happy path
# ---------------------------------------------------------------------------

def test_happy_path_concepts_flow_through_every_stage():
    build = _build_claims("Skewness and kurtosis are observables.", ontology=_ONTOLOGY)
    claim = build.claims[0]
    assert claim.is_atomic
    assert {c.normalized for c in claim.concepts} >= {"Skewness", "Kurtosis"}
    assert claim.concept_assignment_status == "source_backed"

    # A derivation component with a linked equation symbol (math concept) plus the
    # claim's named concepts -> well-formed, no role mismatch.
    components = _enrich(
        [_component("comp_1", linked_claim_ids=[claim.claim_id])],
        _available_claims(build),
        equations=[{"equation_id": "eq_c", "defined_symbols": [{"symbol": "b_2"}], "used_symbols": []},
                   {"equation_id": "eq_d", "defined_symbols": [], "used_symbols": ["b_2"]}],
    )
    comp = components.components[0]
    # claim concepts + equation symbol propagated into the component
    assert {"Skewness", "Kurtosis"} <= set(comp.concepts)
    assert "b_2" in comp.concepts
    assert {"Skewness", "Kurtosis"} <= set(comp.introduced_concepts)

    # component graph rollup
    graph = _graph(components)
    main = [n for n in graph.nodes if n.graph_layer == "main"]
    assert main
    rolled = {c for n in main for c in n.concepts}
    assert {"Skewness", "Kurtosis"} <= rolled

    # course mapping
    course = _course(components)
    topic = course.topics[0]
    assert {"Skewness", "Kurtosis"} <= set(topic.introduced_concepts)

    # export validation: block present, no concept-level warnings
    result = _export(build, components, course)
    block = result.concept_validation
    for key in (
        "missing_concepts", "empty_concepts_on_main_claims",
        "empty_concepts_on_main_components", "concepts_on_composite_claims",
        "concept_role_mismatch", "concepts_from_low_confidence_sources",
    ):
        assert key in block
    assert block["concept_role_mismatch"] == []
    assert block["empty_concepts_on_main_claims"] == []
    assert block["empty_concepts_on_main_components"] == []
    assert not (_CONCEPT_WARNINGS & {w.code for w in result.warnings})


# ---------------------------------------------------------------------------
# Scenario 2: low-confidence path
# ---------------------------------------------------------------------------

_NON_ATOMIC = (
    "The paper derives consistency relations by eliminating nuisance parameters "
    "and shows they can be used to test a target theory."
)


def test_low_confidence_path_surfaces_review_required_concepts():
    # The deterministic split produces claims that carry concepts but are
    # review_required (low-confidence source).
    build = _build_claims(_NON_ATOMIC, claim_type="result", ontology=_ONTOLOGY)
    low = [
        c for c in build.claims
        if c.concept_assignment_status == "review_required" and c.concepts
    ]
    assert low, "expected at least one review_required claim with concepts"

    # A low-confidence (needs_math_review) equation linked to a component must
    # escalate that component's review_status.
    components = _enrich(
        [_component("comp_low", linked_claim_ids=[], linked_equation_ids=["eq_low"])],
        _available_claims(build),
        equations=[{"equation_id": "eq_low", "defined_symbols": [{"symbol": "b_2"}],
                    "used_symbols": [], "needs_math_review": True}],
    )
    comp = components.components[0]
    assert "eq_low" in comp.review_required_equation_ids
    assert comp.review_status == "teacher_review_required"

    result = _export(build, components, _course(components))
    flagged = result.concept_validation["concepts_from_low_confidence_sources"]
    assert any(c.claim_id in flagged for c in low)
    assert "CONCEPTS_FROM_LOW_CONFIDENCE_SOURCE" in [r.code for r in result.review_items]


# ---------------------------------------------------------------------------
# Scenario 3: composite claim path
# ---------------------------------------------------------------------------

def test_composite_claim_concepts_are_not_confirmed_and_are_gated():
    build = _build_claims(
        _NON_ATOMIC,
        claim_type="result",
        ontology=_ONTOLOGY,
        atomic_claims=[
            {"text": "Skewness consistency relations are derived.",
             "atomicity": "atomic", "status": "accepted", "claim_type_candidate": "result"},
            {"text": "Kurtosis consistency relations are derived.",
             "atomicity": "atomic", "status": "accepted", "claim_type_candidate": "result"},
        ],
    )
    parent = next(c for c in build.claims if c.atomicity == "composite")
    assert parent.concept_assignment_status == "tentative"

    # A component linking ONLY the composite parent must not absorb its concepts
    # as confirmed component concepts (non-atomic claims are ignored).
    components = _enrich(
        [_component("comp_comp", linked_claim_ids=[parent.claim_id])],
        _available_claims(build),
    )
    # tentative parent concepts are not propagated as confirmed concepts
    assert parent.concept_assignment_status == "tentative"

    # The export gate flags a composite claim whenever its concepts were wrongly
    # confirmed source_backed (contract violation).
    parent.concept_assignment_status = "source_backed"
    result = _export(build, components, _course(components))
    assert parent.claim_id in result.concept_validation["concepts_on_composite_claims"]
    assert "CONCEPTS_ON_COMPOSITE_CLAIM" in [r.code for r in result.review_items]


# ---------------------------------------------------------------------------
# Scenario 4: missing concepts path
# ---------------------------------------------------------------------------

def test_missing_concepts_path_reports_without_crashing():
    # A main-result claim whose text has no recognizable concept terms.
    build = _build_claims("An untyped statement with no recognizable terms.", claim_type="result")
    claim = build.claims[0]
    assert claim.concepts == []

    # A component with too few concepts (the linked claim has none).
    components = _enrich(
        [_component("comp_thin", linked_claim_ids=[claim.claim_id])],
        _available_claims(build),
    )
    assert len(components.components[0].concepts) < 2

    result = _export(build, components, _course(components))
    block = result.concept_validation
    assert claim.claim_id in block["empty_concepts_on_main_claims"]
    assert claim.claim_id in block["missing_concepts"]
    assert "comp_thin" in block["empty_concepts_on_main_components"]
    assert "comp_thin" in block["missing_concepts"]
    assert "MAIN_CLAIM_INSUFFICIENT_CONCEPTS" in [w.code for w in result.warnings]
    assert "MAIN_COMPONENT_INSUFFICIENT_CONCEPTS" in [w.code for w in result.warnings]
    # pipeline still produced a result object
    assert result.status in {"passed", "passed_with_warnings", "needs_review", "failed_validation"}
