"""Tests for vocabulary unification + section_title (issue #359)."""
from __future__ import annotations

from episteme_graph.agents.claim_object_builder import ClaimObjectBuilder
from episteme_graph.agents.claim_object_builder.schema import (
    CANONICAL_ATOMICITY_VALUES,
    ClaimObjectBuildResult,
    derive_support_status,
    normalize_atomicity,
)
from episteme_graph.agents.claim_qualification.schema import QualifiedSpanRecord
from episteme_graph.agents.component_assembly.input_builder import (
    _attach_claim_object_metadata as attach_component_metadata,
)
from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.dsl_linking.input_builder import (
    _attach_claim_object_metadata as attach_dsl_metadata,
)
from episteme_graph.agents.evidence_registry import EvidenceRegistryBuilder


# ---------------------------------------------------------------------------
# atomicity normalization
# ---------------------------------------------------------------------------

def test_normalize_atomicity_maps_legacy_aliases():
    assert normalize_atomicity("compound") == "composite"
    assert normalize_atomicity("non_atomic") == "split_required"
    assert normalize_atomicity("split_pending") == "split_required"


def test_normalize_atomicity_keeps_canonical_values():
    for value in CANONICAL_ATOMICITY_VALUES:
        assert normalize_atomicity(value) == value


def test_normalize_atomicity_defaults_unknown_to_atomic():
    assert normalize_atomicity("") == "atomic"
    assert normalize_atomicity(None) == "atomic"
    assert normalize_atomicity("banana") == "atomic"


def test_from_dict_converts_legacy_atomicity():
    payload = {
        "document_id": "doc",
        "cartridge_id": None,
        "claims": [
            {"claim_id": "c1", "document_id": "doc", "claim_type": "result",
             "text": "t", "source_evidence_ids": [], "source_span_ids": [],
             "concepts": [], "atomicity": "split_pending", "is_atomic": False},
            {"claim_id": "c2", "document_id": "doc", "claim_type": "result",
             "text": "t", "source_evidence_ids": [], "source_span_ids": [],
             "concepts": [], "atomicity": "compound", "is_atomic": False},
        ],
        "validation_issues": [],
    }
    result = ClaimObjectBuildResult.from_dict(payload)
    assert result.claims[0].atomicity == "split_required"
    assert result.claims[1].atomicity == "composite"
    # 出力 JSON には legacy 語彙が現れない
    for claim in result.to_dict()["claims"]:
        assert claim["atomicity"] in CANONICAL_ATOMICITY_VALUES


# ---------------------------------------------------------------------------
# evidence_adequacy → support_status mapping
# ---------------------------------------------------------------------------

def test_derive_support_status_mapping():
    assert derive_support_status(True, "sufficient") == "source_backed"
    assert derive_support_status(True, "weak") == "partially_source_backed"
    assert derive_support_status(True, "broken") == "review_required"
    assert derive_support_status(True, None) == "source_backed"
    assert derive_support_status(False, "sufficient") == "inferred"


# ---------------------------------------------------------------------------
# builder integration: section_title + support_status
# ---------------------------------------------------------------------------

def _span(span_id, block_id, text, *, adequacy="sufficient"):
    return QualifiedSpanRecord(
        span_id=span_id,
        block_id=block_id,
        section_id="doc_test:sec_3_1",
        text=text,
        role_labels=["approximation"],
        qualification={
            "status": "accepted",
            "claim_type_candidate": "approximation",
            "claim_tier": "paper_core",
            "granularity": "good",
            "evidence_adequacy": adequacy,
        },
        edit_suggestions={"normalized_text": text},
        reason="auto-derived",
        confidence=0.82,
    )


def _structure(block_id, text):
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/x.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(pages=10),
        sections=[Section("doc_test:sec_3_1", "Methods", 1, 4, 5)],
        blocks=[TypedBlock(
            block_id=block_id, page=4, order=65, text=text,
            block_type="body_paragraph", section_id="doc_test:sec_3_1",
        )],
    )


def _build(spans, structure):
    ev_builder = EvidenceRegistryBuilder(structure)
    for block in structure.blocks:
        ev_builder.add_for_block(block.block_id)
    registry = ev_builder.build("doc_test")
    builder = ClaimObjectBuilder(
        evidence_registry=registry, document_structure=structure,
    )
    return builder.build("doc_test", spans)


def test_claim_carries_section_title():
    structure = _structure("blk_1", "Take the zero-recoil limit in the analysis.")
    result = _build([_span("s1", "blk_1", "Take the zero-recoil limit.")], structure)
    assert result.claims[0].section_id == "doc_test:sec_3_1"
    assert result.claims[0].section_title == "Methods"
    # JSON round trip keeps the title
    reloaded = ClaimObjectBuildResult.from_dict(result.to_dict())
    assert reloaded.claims[0].section_title == "Methods"


def test_builder_without_structure_leaves_title_none():
    structure = _structure("blk_1", "text")
    ev_builder = EvidenceRegistryBuilder(structure)
    ev_builder.add_for_block("blk_1")
    registry = ev_builder.build("doc_test")
    builder = ClaimObjectBuilder(evidence_registry=registry)
    result = builder.build("doc_test", [_span("s1", "blk_1", "Take the limit.")])
    assert result.claims[0].section_title is None


def test_broken_evidence_adequacy_caps_support_status():
    structure = _structure("blk_1", "text for the claim")
    result = _build(
        [_span("s1", "blk_1", "Take the limit.", adequacy="broken")], structure,
    )
    assert result.claims[0].support_status == "review_required"


def test_weak_evidence_adequacy_is_partially_source_backed():
    structure = _structure("blk_1", "text for the claim")
    result = _build(
        [_span("s1", "blk_1", "Take the limit.", adequacy="weak")], structure,
    )
    assert result.claims[0].support_status == "partially_source_backed"


def test_sufficient_evidence_stays_source_backed():
    structure = _structure("blk_1", "text for the claim")
    result = _build([_span("s1", "blk_1", "Take the limit.")], structure)
    assert result.claims[0].support_status == "source_backed"


# ---------------------------------------------------------------------------
# section_title propagation into downstream input rows
# ---------------------------------------------------------------------------

class _Claim:
    claim_id = "c1"
    text = "claim text"
    claim_type = "result"
    atomicity = "atomic"
    is_atomic = True
    split_suggestions: list = []
    source_evidence_ids = ["ev_1"]
    qualification_reason = None
    confidence = 0.8
    section_title = "Methods"
    concepts: list = []
    concept_assignment_status = "source_backed"


def test_section_title_propagates_to_dsl_rows():
    row: dict = {"text": "", "claim_type_candidate": "unknown"}
    attach_dsl_metadata(row, _Claim())
    assert row["section_title"] == "Methods"


def test_section_title_propagates_to_component_rows():
    row: dict = {"text": "", "claim_type_candidate": "unknown"}
    attach_component_metadata(row, _Claim())
    assert row["section_title"] == "Methods"


def test_section_title_propagates_to_thesis_rows():
    """thesis_reconstruction の入力行にも section_title が付く（#359 レビュー対応）。"""
    from episteme_graph.agents.thesis_reconstruction.input_builder import (
        ThesisReconstructionInputBuilder,
    )

    class _ClaimObj:
        claim_id = "claim:blk_1:s1"
        section_title = "Methods"

    class _ClaimObjects:
        claims = [_ClaimObj()]

    selection = ThesisReconstructionInputBuilder._accepted_claims(
        type("_Q", (), {"qualified_spans": [_span("s1", "blk_1", "Take the limit.")]})(),
        10,
        claim_objects=_ClaimObjects(),
    )
    assert selection.selected[0]["section_title"] == "Methods"
