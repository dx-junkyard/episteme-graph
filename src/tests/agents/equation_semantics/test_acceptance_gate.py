"""Tests for EquationAcceptanceGate (Issue #245)."""
import pytest

from episteme_graph.agents.equation_semantics.acceptance_gate import EquationAcceptanceGate
from episteme_graph.agents.equation_semantics.schema import EquationCandidate


def _make_candidate(raw_text: str, block_id: str = "blk_1", page: int = 1, section_id: str = "sec_1") -> EquationCandidate:
    return EquationCandidate(
        candidate_id=f"eqcand_{block_id}",
        document_id="doc_test",
        source_location={"page": page, "section_id": section_id, "block_id": block_id, "bbox": []},
        raw_text=raw_text,
        matched_label=None,
        detection_method=["document_structure_equation_block"],
        candidate_score=1.0,
        extraction_status="unparsed",
        acceptance_status="rejected",
        needs_math_review=False,
        review_reason=[],
    )


GATE = EquationAcceptanceGate()


# ------------------------------------------------------------------
# label_only → rejected
# ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "(3.14)",
    "(A.2)",
    " (1) ",
    "(B.10)",
])
def test_label_only_is_rejected(text):
    c = _make_candidate(text)
    result = GATE.process([c])
    assert result[0].extraction_status == "label_only"
    assert result[0].acceptance_status == "rejected"
    assert "label_only_candidate" in result[0].review_reason


# ------------------------------------------------------------------
# fragment_only → needs_merge
# ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "x",
    "+",
    "=",
    "i",
    "∑",
    "∫",
    "lim",
])
def test_fragment_only_is_needs_merge(text):
    c = _make_candidate(text)
    result = GATE.process([c])
    assert result[0].extraction_status == "fragment_only"
    assert result[0].acceptance_status == "needs_merge"
    assert "fragment_only_candidate" in result[0].review_reason


# ------------------------------------------------------------------
# short but complete (has relation operator) → accepted
# ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "x = 0",
    "m = 1",
    "Q^2 > 0",
    "a = b",
])
def test_short_with_relation_is_accepted(text):
    c = _make_candidate(text)
    result = GATE.process([c])
    assert result[0].acceptance_status == "accepted"


# ------------------------------------------------------------------
# equation with body → accepted
# ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "N = a + b (1.1)",
    "RΛc = RD + RD* (3.14)",
    "∫ f dx = F (1.2)",
    "Gamma_tot = sum_i Gamma_i",
    "E = mc^2",
])
def test_equation_with_body_is_accepted(text):
    c = _make_candidate(text)
    result = GATE.process([c])
    assert result[0].acceptance_status == "accepted"
    assert result[0].extraction_status in ("complete", "partial")


# ------------------------------------------------------------------
# missing text → context_only
# ------------------------------------------------------------------

def test_empty_text_is_context_only():
    c = _make_candidate("")
    result = GATE.process([c])
    assert result[0].extraction_status == "missing"
    assert result[0].acceptance_status == "context_only"
    assert "equation_body_missing" in result[0].review_reason


# ------------------------------------------------------------------
# merge hint propagation
# ------------------------------------------------------------------

def test_fragment_gets_merge_hint_from_adjacent_accepted():
    candidates = [
        _make_candidate("N = a + b (1.1)", block_id="blk_0", page=1, section_id="sec_1"),
        _make_candidate("+", block_id="blk_1", page=1, section_id="sec_1"),
    ]
    result = GATE.process(candidates)
    fragment = next(c for c in result if c.source_location["block_id"] == "blk_1")
    accepted = next(c for c in result if c.source_location["block_id"] == "blk_0")
    assert fragment.acceptance_status == "needs_merge"
    assert fragment.merge_target_hint == accepted.candidate_id


def test_fragment_no_merge_hint_across_sections():
    candidates = [
        _make_candidate("N = a + b (1.1)", block_id="blk_0", page=1, section_id="sec_1"),
        _make_candidate("+", block_id="blk_1", page=2, section_id="sec_2"),
    ]
    result = GATE.process(candidates)
    fragment = next(c for c in result if c.source_location["block_id"] == "blk_1")
    assert fragment.merge_target_hint is None


# ------------------------------------------------------------------
# process returns all candidates (accepted + rejected)
# ------------------------------------------------------------------

def test_process_returns_all_candidates():
    candidates = [
        _make_candidate("N = a + b (1.1)", block_id="blk_0"),
        _make_candidate("(3.14)", block_id="blk_1"),
        _make_candidate("x", block_id="blk_2"),
    ]
    result = GATE.process(candidates)
    assert len(result) == 3
    statuses = {c.source_location["block_id"]: c.acceptance_status for c in result}
    assert statuses["blk_0"] == "accepted"
    assert statuses["blk_1"] == "rejected"
    assert statuses["blk_2"] == "needs_merge"


# ------------------------------------------------------------------
# Issue #368: table-derived candidates must not auto-confirm
# ------------------------------------------------------------------

def test_table_provenance_candidate_is_not_auto_confirmed():
    c = _make_candidate("a = b + c (2.1)", block_id="blk_t1")
    c.source_location["table_provenance"] = {"table_id": "tbl_3", "row": 2}
    result = GATE.process([c])[0]
    assert result.acceptance_status != "accepted"
    assert "table_derived_equation_candidate" in result.review_reason


def test_coefficient_row_is_flagged_as_table_derived():
    c = _make_candidate("0.12  0.34  0.56  0.78", block_id="blk_t2")
    result = GATE.process([c])[0]
    assert result.acceptance_status != "accepted"
    assert "table_derived_equation_candidate" in result.review_reason


def test_normal_equation_is_not_flagged_as_table():
    c = _make_candidate("E = m c^2 (3.1)", block_id="blk_n1")
    result = GATE.process([c])[0]
    assert "table_derived_equation_candidate" not in result.review_reason
