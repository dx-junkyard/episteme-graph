"""Tests for the shared claim input selection policy (issue #356)."""
from episteme_graph.agents.claim_selection import (
    RULE_CLAIM_INPUT_LIMIT_EXCEEDED,
    RULE_THESIS_REFERENCED_CLAIM_EXCLUDED,
    select_claim_rows,
    selection_issue_payloads,
    thesis_claim_id_set,
)


def _row(claim_id, *, tier="background", confidence=0.5, span_id=""):
    return {
        "claim_id": claim_id,
        "span_id": span_id or f"span_{claim_id}",
        "claim_tier": tier,
        "confidence": confidence,
        "text": f"text for {claim_id}",
    }


def test_under_limit_keeps_everything():
    rows = [_row("c1"), _row("c2")]
    selection = select_claim_rows(rows, 5, stage="thesis_reconstruction")
    assert selection.selected == rows
    assert selection.excluded == []


def test_paper_core_claims_survive_the_limit():
    """paper_core が末尾にあっても上限超過時に必ず選抜される."""
    rows = [_row(f"bg_{i}", tier="background", confidence=0.9) for i in range(5)]
    rows.append(_row("core_1", tier="paper_core", confidence=0.1))
    selection = select_claim_rows(rows, 3, stage="thesis_reconstruction")
    selected_ids = [r["claim_id"] for r in selection.selected]
    assert "core_1" in selected_ids
    assert len(selected_ids) == 3


def test_higher_confidence_wins_within_same_tier():
    rows = [
        _row("low", tier="background", confidence=0.2),
        _row("high", tier="background", confidence=0.9),
        _row("mid", tier="background", confidence=0.5),
    ]
    selection = select_claim_rows(rows, 2, stage="dsl_linking")
    selected_ids = [r["claim_id"] for r in selection.selected]
    assert "high" in selected_ids and "mid" in selected_ids
    assert "low" not in selected_ids


def test_selected_rows_keep_original_document_order():
    rows = [
        _row("a", tier="background", confidence=0.3),
        _row("b", tier="paper_core", confidence=0.9),
        _row("c", tier="background", confidence=0.8),
    ]
    selection = select_claim_rows(rows, 2, stage="dsl_linking")
    assert [r["claim_id"] for r in selection.selected] == ["b", "c"]


def test_excluded_entries_record_stage_and_reason():
    rows = [_row("keep", tier="paper_core"), _row("drop", tier="meta")]
    selection = select_claim_rows(rows, 1, stage="component_assembly")
    assert len(selection.excluded) == 1
    entry = selection.excluded[0]
    assert entry["claim_id"] == "drop"
    assert entry["excluded_at_stage"] == "component_assembly"
    assert entry["reason"] == "max_claims_exceeded"
    assert entry["referenced_by_thesis"] is False


def test_thesis_referenced_exclusion_is_flagged():
    rows = [_row("keep", tier="paper_core"), _row("thesis_ref", tier="meta")]
    selection = select_claim_rows(
        rows, 1, stage="dsl_linking", thesis_claim_ids={"thesis_ref"}
    )
    assert selection.excluded[0]["referenced_by_thesis"] is True


def test_selection_is_deterministic():
    rows = [
        _row(f"c{i}", tier=t, confidence=c)
        for i, (t, c) in enumerate([
            ("background", 0.5), ("paper_core", 0.4), ("meta", 0.9),
            ("paper_supporting", 0.7), ("background", 0.5),
        ])
    ]
    first = select_claim_rows(rows, 3, stage="thesis_reconstruction")
    second = select_claim_rows(list(rows), 3, stage="thesis_reconstruction")
    assert [r["claim_id"] for r in first.selected] == [
        r["claim_id"] for r in second.selected
    ]
    assert first.excluded == second.excluded


def test_unknown_tier_ranks_with_background():
    rows = [
        _row("unknown_tier", tier="", confidence=0.5),
        _row("prior", tier="prior_work", confidence=0.9),
    ]
    selection = select_claim_rows(rows, 1, stage="dsl_linking")
    assert selection.selected[0]["claim_id"] == "unknown_tier"


def test_issue_payloads_for_exclusions():
    rows = [_row("keep", tier="paper_core"), _row("drop", tier="meta")]
    selection = select_claim_rows(
        rows, 1, stage="dsl_linking", thesis_claim_ids={"drop"}
    )
    payloads = selection_issue_payloads(selection.excluded, stage="dsl_linking")
    rule_ids = [p["rule_id"] for p in payloads]
    assert RULE_CLAIM_INPUT_LIMIT_EXCEEDED in rule_ids
    assert RULE_THESIS_REFERENCED_CLAIM_EXCLUDED in rule_ids
    assert all(p["severity"] == "warning" for p in payloads)
    assert any("drop" in p["message"] for p in payloads)


def test_issue_payloads_empty_when_nothing_excluded():
    assert selection_issue_payloads([], stage="dsl_linking") == []


class _Thesis:
    def __init__(self):
        self.central_thesis = {"claim_ids": ["claim_central"]}
        self.support_structure = {
            "assumptions": [{"claim_ids": ["claim_assume"]}],
            "derivation_core": "not-a-list",
        }


def test_thesis_claim_id_set_collects_all_sections():
    ids = thesis_claim_id_set(_Thesis())
    assert ids == {"claim_central", "claim_assume"}


def test_thesis_claim_id_set_handles_none():
    assert thesis_claim_id_set(None) == set()
