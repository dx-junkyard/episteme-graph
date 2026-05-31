"""Tests for ClaimObjectBuilder."""
from __future__ import annotations

from episteme_graph.agents.claim_object_builder import (
    ClaimObjectBuilder,
)
from episteme_graph.agents.claim_qualification.schema import QualifiedSpanRecord
from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    TypedBlock,
)
from episteme_graph.agents.evidence_registry import EvidenceRegistryBuilder


def _make_qualified(
    span_id: str,
    block_id: str,
    text: str,
    *,
    status: str = "accepted",
    claim_type: str = "approximation",
    role_labels: list[str] | None = None,
) -> QualifiedSpanRecord:
    return QualifiedSpanRecord(
        span_id=span_id,
        block_id=block_id,
        section_id="doc_test:sec_3_1",
        text=text,
        role_labels=role_labels or ["approximation"],
        qualification={
            "status": status,
            "claim_type_candidate": claim_type,
            "claim_tier": "paper_core",
            "granularity": "good",
            "evidence_adequacy": "sufficient",
        },
        edit_suggestions={"normalized_text": text},
        reason="auto-derived",
        confidence=0.82,
    )


def _make_structure_with(block_id: str, text: str) -> DocumentStructureResult:
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/x.pdf",
        cartridge_id="particle_physics",
        metadata=DocumentMetadata(pages=10),
        sections=[],
        blocks=[
            TypedBlock(
                block_id=block_id,
                page=4,
                order=65,
                text=text,
                block_type="body_paragraph",
                section_id="doc_test:sec_3_1",
            ),
        ],
    )


def test_one_claim_per_span():
    structure = _make_structure_with("blk_004_0065", "In the zero-recoil limit ...")
    ev_builder = EvidenceRegistryBuilder(structure)
    ev_builder.add_for_block("blk_004_0065")
    registry = ev_builder.build("doc_test", "particle_physics")

    spans = [
        _make_qualified("span_001", "blk_004_0065", "Take the zero-recoil limit."),
        _make_qualified("span_002", "blk_004_0065", "Apply the heavy quark limit.", claim_type="assumption"),
    ]
    builder = ClaimObjectBuilder(evidence_registry=registry)
    result = builder.build("doc_test", spans, cartridge_id="particle_physics")

    assert len(result.claims) == 2
    assert result.claims[0].claim_type == "approximation"
    assert result.claims[1].claim_type == "assumption"


def test_evidence_ids_are_referenced_not_inlined():
    structure = _make_structure_with("blk_004_0065", "In the zero-recoil limit the relation reduces.")
    ev_builder = EvidenceRegistryBuilder(structure)
    ev_builder.add_for_block("blk_004_0065")
    registry = ev_builder.build("doc_test")

    spans = [_make_qualified("span_001", "blk_004_0065", "Zero-recoil limit applies.")]
    builder = ClaimObjectBuilder(evidence_registry=registry)
    result = builder.build("doc_test", spans)

    claim = result.claims[0]
    assert claim.source_evidence_ids == ["ev_0001"]
    # The Claim text must NOT contain raw evidence (it's a normalized paraphrase)
    assert claim.text == "Zero-recoil limit applies."
    # And the dataclass exposes no evidence_text attribute
    assert not hasattr(claim, "evidence_text")


def test_rejected_spans_are_skipped():
    structure = _make_structure_with("blk_x", "noise")
    builder = ClaimObjectBuilder()
    spans = [
        _make_qualified("span_a", "blk_x", "rejected text", status="rejected"),
        _make_qualified("span_b", "blk_x", "deferred text", status="deferred"),
    ]
    result = builder.build("doc_test", spans)
    assert result.claims == []


def test_concept_resolver_via_cartridge_aliases():
    structure = _make_structure_with(
        "blk_004_0065",
        "We take the zero-recoil limit and apply the heavy quark expansion.",
    )
    ev_builder = EvidenceRegistryBuilder(structure)
    ev_builder.add_for_block("blk_004_0065")
    registry = ev_builder.build("doc_test")

    cartridge_ontology = {
        "aliases": {
            "zero_recoil_limit": ["zero-recoil limit"],
            "heavy_quark_expansion": ["heavy quark expansion", "HQE"],
        },
        "concept_types": {
            "zero_recoil_limit": "Approximation",
            "heavy_quark_expansion": "TheoreticalFramework",
        },
    }

    spans = [
        _make_qualified("span_001", "blk_004_0065",
                        "Take the zero-recoil limit using the heavy quark expansion."),
    ]
    builder = ClaimObjectBuilder(
        evidence_registry=registry,
        cartridge_ontology=cartridge_ontology,
    )
    result = builder.build("doc_test", spans)

    claim = result.claims[0]
    names = [c.normalized for c in claim.concepts]
    assert "zero_recoil_limit" in names
    assert "heavy_quark_expansion" in names


def test_equation_linking_by_label():
    structure = _make_structure_with("blk_x", "By eq. (3.14) we recover the result.")
    ev_builder = EvidenceRegistryBuilder(structure)
    ev_builder.add_for_block("blk_x")
    registry = ev_builder.build("doc_test")

    equation_index = {
        "eq_3_14": {"label": "3.14"},
    }

    spans = [_make_qualified("span_001", "blk_x", "By eq. (3.14) we recover the result.",
                             claim_type="equation_relation")]
    builder = ClaimObjectBuilder(evidence_registry=registry, equation_index=equation_index)
    result = builder.build("doc_test", spans)

    assert result.claims[0].equation_ids == ["eq_3_14"]


def test_validation_flags_missing_concepts():
    structure = _make_structure_with("blk_x", "Some claim text without aliases.")
    ev_builder = EvidenceRegistryBuilder(structure)
    ev_builder.add_for_block("blk_x")
    registry = ev_builder.build("doc_test")

    spans = [_make_qualified("span_001", "blk_x", "An untyped claim.")]
    builder = ClaimObjectBuilder(evidence_registry=registry)
    result = builder.build("doc_test", spans)

    rules = {i.rule_id for i in result.validation_issues}
    assert "claim_concepts_empty" in rules


def test_domain_concept_fallback_for_bias_elimination_paper():
    spans = [
        _make_qualified(
            "span_001",
            "blk_x",
            "Skewness and kurtosis consistency relations eliminate nonlinear galaxy bias.",
            claim_type="result",
        )
    ]
    builder = ClaimObjectBuilder()
    result = builder.build("doc_test", spans)

    concepts = {c.normalized for c in result.claims[0].concepts}
    assert {"skewness", "kurtosis", "consistency relation", "nonlinear galaxy bias"} <= concepts
    assert "claim_concepts_empty" not in {i.rule_id for i in result.validation_issues}


def test_split_claims_create_compound_parent_and_atomic_children():
    structure = _make_structure_with("blk_x", "Definition and conclusion in one span.")
    ev_builder = EvidenceRegistryBuilder(structure)
    ev_builder.add_for_block("blk_x")
    registry = ev_builder.build("doc_test")

    span = _make_qualified("span_001", "blk_x", "A is defined, therefore B follows.")
    span.edit_suggestions = {
        "split_claims": [
            {"text": "A is defined.", "claim_type": "definition"},
            {"text": "B follows.", "claim_type": "conclusion"},
        ]
    }
    builder = ClaimObjectBuilder(evidence_registry=registry)
    result = builder.build("doc_test", [span])

    children = [c for c in result.claims if c.parent_claim_id == "claim_span_001"]
    parent = [c for c in result.claims if c.claim_id == "claim_span_001"][0]
    assert parent.atomicity == "compound"
    assert parent.subclaim_ids == ["claim_span_001_sub01", "claim_span_001_sub02"]
    assert [c.atomicity for c in children] == ["atomic", "atomic"]
    assert [c.claim_type for c in children] == ["definition", "conclusion"]
