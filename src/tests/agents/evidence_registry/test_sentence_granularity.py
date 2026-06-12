"""Tests for sentence-level evidence granularity (issue #363)."""
from __future__ import annotations

from episteme_graph.agents.claim_object_builder import ClaimObjectBuilder
from episteme_graph.agents.claim_qualification.schema import QualifiedSpanRecord
from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    TypedBlock,
)
from episteme_graph.agents.evidence_registry import EvidenceRegistryBuilder
from episteme_graph.agents.evidence_registry.builder import (
    split_sentences_with_offsets,
)

_BLOCK_TEXT = (
    "The decay rate vanishes in the heavy-quark limit. "
    "The sum rule relates the three measured ratios. "
    "This was verified numerically."
)


def _structure(block_id="blk_1", text=_BLOCK_TEXT):
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/x.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(pages=1),
        sections=[],
        blocks=[TypedBlock(
            block_id=block_id, page=1, order=1, text=text,
            block_type="body_paragraph", section_id="sec_1",
        )],
    )


# ---------------------------------------------------------------------------
# sentence splitting
# ---------------------------------------------------------------------------

def test_split_sentences_returns_offsets():
    spans = split_sentences_with_offsets(_BLOCK_TEXT)
    sentences = [_BLOCK_TEXT[a:b] for a, b in spans]
    assert sentences == [
        "The decay rate vanishes in the heavy-quark limit.",
        "The sum rule relates the three measured ratios.",
        "This was verified numerically.",
    ]


def test_split_sentences_guards_abbreviations_and_decimals():
    text = "Combining Eq. (2.7) with v2.0 of the code gives 3.14 exactly. Done."
    spans = split_sentences_with_offsets(text)
    assert [text[a:b] for a, b in spans] == [
        "Combining Eq. (2.7) with v2.0 of the code gives 3.14 exactly.",
        "Done.",
    ]


# ---------------------------------------------------------------------------
# registry records
# ---------------------------------------------------------------------------

def _registry_with_sentences():
    structure = _structure()
    builder = EvidenceRegistryBuilder(structure)
    parent_id = builder.add_for_block("blk_1", public_export_policy="snippet_allowed")
    builder.add_sentences_for_block("blk_1", parent_evidence_id=parent_id)
    return builder.build("doc_test"), parent_id


def test_sentence_records_link_to_parent_block_record():
    registry, parent_id = _registry_with_sentences()
    sentence_records = [
        r for r in registry.records if r.evidence_role == "sentence_quote"
    ]
    assert len(sentence_records) == 3
    for record in sentence_records:
        assert record.parent_evidence_id == parent_id
        assert record.source.block_id == "blk_1"
        assert _BLOCK_TEXT[record.source.span_start:record.source.span_end] == record.evidence_text


def test_sentence_records_inherit_export_policy():
    registry, _ = _registry_with_sentences()
    for record in registry.records:
        if record.evidence_role == "sentence_quote":
            assert record.public_export_policy == "snippet_allowed"


def test_single_sentence_block_adds_no_sentence_records():
    structure = _structure(text="Only one sentence here.")
    builder = EvidenceRegistryBuilder(structure)
    parent_id = builder.add_for_block("blk_1")
    assert builder.add_sentences_for_block("blk_1", parent_evidence_id=parent_id) == []


def test_sentence_records_round_trip_via_dict():
    from episteme_graph.agents.evidence_registry.schema import EvidenceRegistryResult
    registry, parent_id = _registry_with_sentences()
    reloaded = EvidenceRegistryResult.from_dict(registry.to_dict())
    sentence_records = [
        r for r in reloaded.records if r.evidence_role == "sentence_quote"
    ]
    assert sentence_records and all(
        r.parent_evidence_id == parent_id for r in sentence_records
    )


# ---------------------------------------------------------------------------
# atomic claim → sentence evidence refinement
# ---------------------------------------------------------------------------

def _span_with_atomic_claims(atomic_claims):
    return QualifiedSpanRecord(
        span_id="s1",
        block_id="blk_1",
        section_id="sec_1",
        text=_BLOCK_TEXT,
        role_labels=["result"],
        qualification={
            "status": "accepted",
            "claim_type_candidate": "result",
            "claim_tier": "paper_core",
            "granularity": "too_broad",
            "evidence_adequacy": "sufficient",
        },
        edit_suggestions={"should_split": True},
        reason="r",
        confidence=0.9,
        atomic_claims=atomic_claims,
    )


def _build_claims(atomic_claims):
    registry, _ = _registry_with_sentences()
    builder = ClaimObjectBuilder(evidence_registry=registry)
    return registry, builder.build("doc_test", [_span_with_atomic_claims(atomic_claims)])


def test_matching_evidence_quote_narrows_to_sentence():
    registry, result = _build_claims([
        {"text": "The decay rate vanishes in the heavy-quark limit.",
         "atomicity": "atomic", "status": "accepted", "source_span_id": "s1",
         "evidence_quote": "The decay rate vanishes in the heavy-quark limit."},
        {"text": "The sum rule relates the three measured ratios.",
         "atomicity": "atomic", "status": "accepted", "source_span_id": "s1",
         "evidence_quote": "The sum rule relates the three measured ratios"},
    ])
    sentence_ids = {
        r.evidence_id: r.evidence_text
        for r in registry.records if r.evidence_role == "sentence_quote"
    }
    children = [c for c in result.claims if c.parent_claim_id]
    assert len(children) == 2
    for child in children:
        assert len(child.source_evidence_ids) == 1
        evidence_id = child.source_evidence_ids[0]
        assert evidence_id in sentence_ids
        # 引用文と claim が同じ文を指す
        assert child.text.rstrip(".") in sentence_ids[evidence_id].rstrip(".") or \
            sentence_ids[evidence_id].rstrip(".") in child.text


def test_unmatched_quote_keeps_block_evidence_with_warning():
    registry, result = _build_claims([
        {"text": "An unrelated rewritten statement.",
         "atomicity": "atomic", "status": "accepted", "source_span_id": "s1",
         "evidence_quote": "totally different wording that matches nothing"},
        {"text": "The sum rule relates the three measured ratios.",
         "atomicity": "atomic", "status": "accepted", "source_span_id": "s1",
         "evidence_quote": ""},
    ])
    block_ids = {
        r.evidence_id for r in registry.records
        if r.evidence_role == "source_quote"
    }
    children = [c for c in result.claims if c.parent_claim_id]
    unmatched = children[0]
    assert set(unmatched.source_evidence_ids) == block_ids  # kept, not dropped
    assert any(
        i.rule_id == "evidence_quote_unmatched" for i in result.validation_issues
    )


def test_empty_quote_keeps_block_evidence_silently():
    registry, result = _build_claims([
        {"text": "The sum rule relates the ratios.", "atomicity": "atomic",
         "status": "accepted", "source_span_id": "s1", "evidence_quote": ""},
        {"text": "This was verified numerically by the authors.",
         "atomicity": "atomic", "status": "accepted", "source_span_id": "s1",
         "evidence_quote": ""},
    ])
    assert not any(
        i.rule_id == "evidence_quote_unmatched" for i in result.validation_issues
    )
