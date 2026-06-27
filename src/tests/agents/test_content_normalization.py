"""Tests for deterministic content hashing (issue #362)."""
from __future__ import annotations

from episteme_graph.agents.claim_object_builder import ClaimObjectBuilder
from episteme_graph.agents.claim_object_builder.schema import (
    ClaimObjectBuildResult,
)
from episteme_graph.agents.claim_qualification.schema import QualifiedSpanRecord
from episteme_graph.agents.content_normalization import (
    CONTENT_HASH_VERSION,
    claim_content_hash,
    content_hash,
    equation_content_hash,
    normalize_equation_for_hash,
    normalize_text_for_hash,
)
from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    TypedBlock,
)
from episteme_graph.agents.equation_semantics.schema import (
    EquationSemanticsResult,
)
from episteme_graph.agents.equation_semantics.validator import (
    EquationSemanticsValidator,
)
from episteme_graph.agents.evidence_registry import EvidenceRegistryBuilder


# ---------------------------------------------------------------------------
# normalization rules
# ---------------------------------------------------------------------------

def test_whitespace_and_punctuation_variants_share_a_hash():
    a = claim_content_hash("The decay rate  vanishes\n in the heavy-quark limit.")
    b = claim_content_hash("The decay rate vanishes in the heavy-quark limit")
    assert a == b != ""


def test_case_folding_for_claim_text():
    assert claim_content_hash("The Sum Rule holds.") == claim_content_hash(
        "the sum rule holds"
    )


def test_math_symbols_in_claims_keep_case():
    """記号・数式部分は小文字化しない (issue #362)。"""
    assert claim_content_hash("The ratio R_D is measured.") != claim_content_hash(
        "The ratio r_d is measured."
    )
    assert claim_content_hash("The coupling B vanishes.") != claim_content_hash(
        "The coupling b vanishes."
    )
    assert claim_content_hash("DHOST theories survive.") != claim_content_hash(
        "dhost theories survive."
    )


def test_different_claims_have_different_hashes():
    assert claim_content_hash("The rate vanishes.") != claim_content_hash(
        "The rate diverges."
    )


def test_equation_normalization_ignores_whitespace_and_math_mode():
    a = equation_content_hash("$R_D = a + b$")
    b = equation_content_hash("R_D=a+b")
    c = equation_content_hash(None, "R_D = a + b")
    assert a == b == c != ""


def test_equation_normalization_preserves_case():
    assert equation_content_hash("b = 1") != equation_content_hash("B = 1")


def test_hash_is_deterministic_and_versioned():
    assert content_hash(normalize_text_for_hash("x")) == content_hash(
        normalize_text_for_hash("x")
    )
    assert content_hash("") == ""
    # v2: prose-token casefolding replaced the v1 full lowercasing — old hashes
    # are incompatible, so the version must have been bumped (#362 レビュー対応).
    assert isinstance(CONTENT_HASH_VERSION, int) and CONTENT_HASH_VERSION >= 2


def test_normalize_equation_empty_input():
    assert normalize_equation_for_hash(None, None) == ""
    assert equation_content_hash(None, None) == ""


# ---------------------------------------------------------------------------
# claim builder integration
# ---------------------------------------------------------------------------

def _span(span_id, block_id, text):
    return QualifiedSpanRecord(
        span_id=span_id,
        block_id=block_id,
        section_id="sec_1",
        text=text,
        role_labels=["approximation"],
        qualification={
            "status": "accepted",
            "claim_type_candidate": "approximation",
            "claim_tier": "paper_core",
            "granularity": "good",
            "evidence_adequacy": "sufficient",
        },
        edit_suggestions={"normalized_text": text},
        reason="r",
        confidence=0.8,
    )


def _structure(block_id):
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/x.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(pages=1),
        sections=[],
        blocks=[TypedBlock(
            block_id=block_id, page=1, order=1, text="source text",
            block_type="body_paragraph", section_id="sec_1",
        )],
    )


def _build(spans):
    structure = _structure("blk_1")
    ev_builder = EvidenceRegistryBuilder(structure)
    ev_builder.add_for_block("blk_1")
    registry = ev_builder.build("doc_test")
    return ClaimObjectBuilder(evidence_registry=registry).build("doc_test", spans)


def test_built_claims_carry_content_hash_and_version():
    result = _build([_span("s1", "blk_1", "Take the zero-recoil limit.")])
    claim = result.claims[0]
    assert claim.content_hash
    assert claim.content_hash_version == CONTENT_HASH_VERSION
    assert claim.content_hash == claim_content_hash(claim.normalized_text)


def test_duplicate_claim_hashes_are_reported_not_merged():
    result = _build([
        _span("s1", "blk_1", "Take the zero-recoil limit."),
        _span("s2", "blk_1", "Take  the zero-recoil limit"),  # whitespace variant
    ])
    assert len(result.claims) == 2  # never auto-merged
    duplicates = [
        i for i in result.validation_issues
        if i.rule_id == "duplicate_claim_content_hash"
    ]
    assert len(duplicates) == 1
    assert duplicates[0].severity == "warning"


def test_legacy_claim_payload_without_hash_loads():
    payload = {
        "document_id": "doc",
        "cartridge_id": None,
        "claims": [
            {"claim_id": "c1", "document_id": "doc", "claim_type": "result",
             "text": "t", "source_evidence_ids": [], "source_span_ids": [],
             "concepts": []},
        ],
        "validation_issues": [],
    }
    result = ClaimObjectBuildResult.from_dict(payload)
    assert result.claims[0].content_hash == ""
    assert result.claims[0].content_hash_version == 0


# ---------------------------------------------------------------------------
# equation integration
# ---------------------------------------------------------------------------

def test_equation_records_round_trip_hash_fields():
    payload = {
        "document_id": "doc",
        "cartridge_id": None,
        "equation_candidates": [],
        "equations": [{
            "equation_id": "eq_1",
            "document_id": "doc",
            "label": "1",
            "candidate_trace_ids": [],
            "source_extraction": {
                "raw_text": "a = b", "latex": "a = b", "plain_text": "a = b",
                "source_location": {"page": 1, "section_id": "s", "block_id": "b"},
                "extraction_source": "pdf_text_layer",
                "extraction_status": "complete",
            },
            "reconstruction": {
                "latex": None, "plain_text": None, "status": "none",
                "method": [], "supporting_refs": [], "confidence": 0.0,
                "review_required": False,
            },
            "semantics": {"equation_type": "relation"},
            "confidence_policy": {"can_support_claim": True},
            "content_hash": "abc123",
            "content_hash_version": 1,
        }],
        "validation_issues": [],
    }
    result = EquationSemanticsResult.from_dict(payload)
    assert result.equations[0].content_hash == "abc123"
    assert result.equations[0].content_hash_version == 1


def test_equation_validator_reports_duplicate_hashes():
    payload_eq = {
        "equation_id": "eq_1",
        "document_id": "doc",
        "label": "1",
        "candidate_trace_ids": ["cand_1"],
        "source_extraction": {
            "raw_text": "a = b", "latex": "a = b", "plain_text": "a = b",
            "source_location": {"page": 1, "section_id": "s", "block_id": "b"},
            "extraction_source": "pdf_text_layer",
            "extraction_status": "complete",
        },
        "reconstruction": {
            "latex": None, "plain_text": None, "status": "none",
            "method": [], "supporting_refs": [], "confidence": 0.0,
            "review_required": False,
        },
        "semantics": {"equation_type": "relation", "semantic_status": "source_backed",
                      "confidence": 0.9},
        "confidence_policy": {"can_support_claim": True},
        "content_hash": "samehash",
        "content_hash_version": 1,
    }
    second = dict(payload_eq)
    second["equation_id"] = "eq_2"
    result = EquationSemanticsResult.from_dict({
        "document_id": "doc", "cartridge_id": None,
        "equation_candidates": [], "equations": [payload_eq, second],
        "validation_issues": [],
    })
    issues = EquationSemanticsValidator().validate(result)
    duplicates = [
        i for i in issues if i.rule_id == "duplicate_equation_content_hash"
    ]
    assert len(duplicates) == 1
    assert duplicates[0].severity == "warning"
