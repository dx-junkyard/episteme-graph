"""Tests for diff-based proposal evaluation (Issue #39).

Covers:
- compute_structure_diff: diff 計算の正確性
- _set_nested_value: ネストされた値の設定
- _parse_diff_decisions: LLM レスポンスのパース
- evaluate_and_merge_proposals: 差分なし早期リターン
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core.schema import (
    AbstractStructure,
    CausalEdge,
    Constraints,
    CorePredicate,
    FieldDiff,
    Hypothesis,
    MergeResult,
    Methodology,
    PaperStructure,
    ProblemStatement,
    ReviewStatus,
)

# テスト用の Lazy import（extractor は OpenAI client を必要とするが、
# diff 関連関数はクライアント不要のためモックでテスト可能）


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_structure(**overrides) -> PaperStructure:
    """テスト用の PaperStructure を生成するヘルパー。"""
    defaults = {
        "paper_id": "2401.00001",
        "title": "Test Paper",
        "problem": ProblemStatement(
            background="Some background",
            problem="Core problem",
        ),
        "hypothesis": Hypothesis(
            statement="Main hypothesis",
            rationale="Because reasons",
        ),
        "methodology": Methodology(
            approach="ML approach",
            techniques=["deep learning", "transformers"],
        ),
        "constraints": Constraints(
            assumptions=["assumption1"],
            limitations=["limitation1"],
        ),
        "abstract_structure": AbstractStructure(
            variables=["X", "Y"],
            edges=[
                CausalEdge(source="X", target="Y", core_predicate=CorePredicate.CAUSES, domain_verb="causes", polarity="+"),
            ],
            smiles_dsl="(x:Agent:X) -[CAUSES:causes:+]-> (y:Resource:Y)",
        ),
    }
    defaults.update(overrides)
    return PaperStructure(**defaults)


# ---------------------------------------------------------------------------
# compute_structure_diff
# ---------------------------------------------------------------------------

class TestComputeStructureDiff:
    def test_no_diff_returns_empty(self):
        from core.extractor import compute_structure_diff

        base = _make_structure()
        proposed = _make_structure()
        assert compute_structure_diff(base, proposed) == []

    def test_simple_field_change(self):
        from core.extractor import compute_structure_diff

        base = _make_structure()
        proposed = _make_structure(title="Updated Title")
        diffs = compute_structure_diff(base, proposed)
        assert len(diffs) == 1
        assert diffs[0].field_path == "title"
        assert diffs[0].base_value == "Test Paper"
        assert diffs[0].proposed_value == "Updated Title"

    def test_nested_field_change(self):
        from core.extractor import compute_structure_diff

        base = _make_structure()
        proposed = _make_structure(
            hypothesis=Hypothesis(
                statement="Revised hypothesis",
                rationale="Because reasons",
            )
        )
        diffs = compute_structure_diff(base, proposed)
        assert len(diffs) == 1
        assert diffs[0].field_path == "hypothesis.statement"

    def test_list_field_change(self):
        from core.extractor import compute_structure_diff

        base = _make_structure()
        proposed = _make_structure(
            methodology=Methodology(
                approach="ML approach",
                techniques=["deep learning", "transformers", "attention"],
            )
        )
        diffs = compute_structure_diff(base, proposed)
        assert len(diffs) == 1
        assert diffs[0].field_path == "methodology.techniques"

    def test_paper_id_excluded(self):
        """paper_id の変更は diff に含まれない。"""
        from core.extractor import compute_structure_diff

        base = _make_structure()
        proposed = _make_structure(paper_id="different_id")
        diffs = compute_structure_diff(base, proposed)
        assert len(diffs) == 0

    def test_review_status_excluded(self):
        """review_status の変更は diff に含まれない。"""
        from core.extractor import compute_structure_diff

        base = _make_structure()
        proposed = _make_structure()
        proposed.review_status = ReviewStatus.APPROVED
        diffs = compute_structure_diff(base, proposed)
        assert len(diffs) == 0

    def test_multiple_changes(self):
        from core.extractor import compute_structure_diff

        base = _make_structure()
        proposed = _make_structure(
            title="New Title",
            hypothesis=Hypothesis(statement="New hyp", rationale="New rationale"),
        )
        diffs = compute_structure_diff(base, proposed)
        paths = {d.field_path for d in diffs}
        assert "title" in paths
        assert "hypothesis.statement" in paths
        assert "hypothesis.rationale" in paths

    def test_smiles_dsl_preserved_when_unchanged(self):
        """smiles_dsl が変更されていなければ diff に含まれない。"""
        from core.extractor import compute_structure_diff

        dsl = "(a:Agent:X) -[causes:+]-> (r:Resource:Y)"
        base = _make_structure(
            abstract_structure=AbstractStructure(
                variables=["X", "Y"],
                edges=[],
                smiles_dsl=dsl,
            )
        )
        proposed = _make_structure(
            title="Changed Title",
            abstract_structure=AbstractStructure(
                variables=["X", "Y"],
                edges=[],
                smiles_dsl=dsl,
            ),
        )
        diffs = compute_structure_diff(base, proposed)
        paths = {d.field_path for d in diffs}
        assert "abstract_structure.smiles_dsl" not in paths
        assert "title" in paths


# ---------------------------------------------------------------------------
# _set_nested_value
# ---------------------------------------------------------------------------

class TestSetNestedValue:
    def test_simple_key(self):
        from core.extractor import _set_nested_value

        d = {"title": "old"}
        _set_nested_value(d, "title", "new")
        assert d["title"] == "new"

    def test_nested_key(self):
        from core.extractor import _set_nested_value

        d = {"problem": {"background": "old", "problem": "p"}}
        _set_nested_value(d, "problem.background", "new bg")
        assert d["problem"]["background"] == "new bg"
        assert d["problem"]["problem"] == "p"  # 他のフィールドは変わらない

    def test_list_value_from_json_string(self):
        from core.extractor import _set_nested_value

        d = {"methodology": {"techniques": ["a", "b"]}}
        _set_nested_value(d, "methodology.techniques", '["a", "b", "c"]')
        assert d["methodology"]["techniques"] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# _parse_diff_decisions
# ---------------------------------------------------------------------------

class TestParseDiffDecisions:
    def test_valid_json(self):
        from core.extractor import _parse_diff_decisions

        raw = json.dumps([
            {"field_path": "title", "action": "accept", "final_value": "New", "reason": "Better"},
            {"field_path": "hypothesis.statement", "action": "reject", "final_value": "", "reason": "Vague"},
        ])
        result = _parse_diff_decisions(raw)
        assert "title" in result
        assert result["title"]["action"] == "accept"
        assert "hypothesis.statement" in result
        assert result["hypothesis.statement"]["action"] == "reject"

    def test_json_with_surrounding_text(self):
        from core.extractor import _parse_diff_decisions

        raw = 'Here is my analysis:\n[{"field_path": "title", "action": "accept", "final_value": "X", "reason": "ok"}]\nDone.'
        result = _parse_diff_decisions(raw)
        assert "title" in result

    def test_invalid_json(self):
        from core.extractor import _parse_diff_decisions

        result = _parse_diff_decisions("not json at all")
        assert result == {}

    def test_empty_array(self):
        from core.extractor import _parse_diff_decisions

        result = _parse_diff_decisions("[]")
        assert result == {}


# ---------------------------------------------------------------------------
# evaluate_and_merge_proposals — early return on no diff
# ---------------------------------------------------------------------------

class TestEvaluateAndMergeNoChanges:
    def test_no_diff_returns_base(self):
        from core.extractor import evaluate_and_merge_proposals

        base = _make_structure()
        proposed = _make_structure()
        result = evaluate_and_merge_proposals(base, proposed)
        assert isinstance(result, MergeResult)
        assert result.merged_structure.title == base.title
        assert "差分がない" in result.evaluation_reasoning


# ---------------------------------------------------------------------------
# evaluate_and_merge_proposals — diff-based merge with mocked LLM
# ---------------------------------------------------------------------------

class TestEvaluateAndMergeDiffBased:
    @patch("metaweave.extractor.get_client")
    @patch("metaweave.extractor.get_settings")
    def test_accept_change_applied(self, mock_settings, mock_client):
        from core.extractor import evaluate_and_merge_proposals

        mock_settings.return_value = MagicMock(analysis_model="test-model")

        # LLM が title の変更を accept するレスポンスを返す
        llm_response = json.dumps([
            {"field_path": "title", "action": "accept", "final_value": "Better Title", "reason": "More descriptive"},
        ])
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=llm_response))]
        mock_client.return_value.chat.completions.create.return_value = mock_resp

        base = _make_structure()
        proposed = _make_structure(title="Better Title")
        result = evaluate_and_merge_proposals(base, proposed)

        assert result.merged_structure.title == "Better Title"
        assert "[ACCEPT] title" in result.evaluation_reasoning

    @patch("metaweave.extractor.get_client")
    @patch("metaweave.extractor.get_settings")
    def test_reject_preserves_base(self, mock_settings, mock_client):
        from core.extractor import evaluate_and_merge_proposals

        mock_settings.return_value = MagicMock(analysis_model="test-model")

        llm_response = json.dumps([
            {"field_path": "title", "action": "reject", "final_value": "", "reason": "Original is better"},
        ])
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=llm_response))]
        mock_client.return_value.chat.completions.create.return_value = mock_resp

        base = _make_structure()
        proposed = _make_structure(title="Worse Title")
        result = evaluate_and_merge_proposals(base, proposed)

        assert result.merged_structure.title == "Test Paper"  # base value preserved
        assert "[REJECT] title" in result.evaluation_reasoning

    @patch("metaweave.extractor.get_client")
    @patch("metaweave.extractor.get_settings")
    def test_unchanged_fields_never_touched(self, mock_settings, mock_client):
        """未変更フィールド (smiles_dsl 等) が LLM ハルシネーションで破損しないことを検証。"""
        from core.extractor import evaluate_and_merge_proposals

        mock_settings.return_value = MagicMock(analysis_model="test-model")

        llm_response = json.dumps([
            {"field_path": "title", "action": "accept", "final_value": "New Title", "reason": "ok"},
        ])
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=llm_response))]
        mock_client.return_value.chat.completions.create.return_value = mock_resp

        original_dsl = "(x:Agent:X) -[CAUSES:causes:+]-> (y:Resource:Y)"
        base = _make_structure()
        proposed = _make_structure(title="New Title")
        result = evaluate_and_merge_proposals(base, proposed)

        # smiles_dsl は diff に含まれず、LLM にも送信されないため、元の値がそのまま残る
        assert result.merged_structure.abstract_structure.smiles_dsl == original_dsl
        # 他の未変更フィールドも保持
        assert result.merged_structure.problem.background == "Some background"
        assert result.merged_structure.hypothesis.statement == "Main hypothesis"
        assert result.merged_structure.methodology.techniques == ["deep learning", "transformers"]

    @patch("metaweave.extractor.get_client")
    @patch("metaweave.extractor.get_settings")
    def test_paper_id_always_preserved(self, mock_settings, mock_client):
        """マージ後も paper_id は必ず base のものが引き継がれる。"""
        from core.extractor import evaluate_and_merge_proposals

        mock_settings.return_value = MagicMock(analysis_model="test-model")

        llm_response = json.dumps([
            {"field_path": "title", "action": "accept", "final_value": "X", "reason": "ok"},
        ])
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content=llm_response))]
        mock_client.return_value.chat.completions.create.return_value = mock_resp

        base = _make_structure(paper_id="2401.00001")
        proposed = _make_structure(paper_id="different", title="X")
        result = evaluate_and_merge_proposals(base, proposed)

        assert result.merged_structure.paper_id == "2401.00001"


# ---------------------------------------------------------------------------
# CausalEdge is_core field tests (Issue #45)
# ---------------------------------------------------------------------------

class TestCausalEdgeIsCore:
    """CausalEdge の is_core フィールドに関するテスト。"""

    def test_default_is_core_true(self):
        """is_core のデフォルト値は True。"""
        edge = CausalEdge(source="A", target="B")
        assert edge.is_core is True

    def test_is_core_false(self):
        """is_core=False を明示指定できる。"""
        edge = CausalEdge(source="A", target="B", is_core=False)
        assert edge.is_core is False

    def test_is_core_serialization(self):
        """is_core, core_predicate, domain_verb が model_dump に含まれる。"""
        edge = CausalEdge(source="A", target="B", core_predicate=CorePredicate.INHIBITS, domain_verb="inhibits", polarity="-", is_core=False)
        d = edge.model_dump()
        assert "is_core" in d
        assert d["is_core"] is False
        assert "core_predicate" in d
        assert d["core_predicate"] == CorePredicate.INHIBITS
        assert "domain_verb" in d
        assert d["domain_verb"] == "inhibits"

    def test_structure_with_mixed_core_edges(self):
        """core と peripheral の混合エッジを含む PaperStructure を構築できる。"""
        structure = _make_structure(
            abstract_structure=AbstractStructure(
                variables=["X", "Y", "Z"],
                edges=[
                    CausalEdge(source="X", target="Y", core_predicate=CorePredicate.CAUSES, domain_verb="causes", polarity="+", is_core=True),
                    CausalEdge(source="Y", target="Z", core_predicate=CorePredicate.CORRELATES, domain_verb="correlates", polarity="+", is_core=False),
                ],
                smiles_dsl="(x:Agent:X) ==[CAUSES:causes:+]=> (y:Resource:Y) (y) -[CORRELATES:correlates:+]-> (z:Event:Z)",
            )
        )
        core_edges = [e for e in structure.abstract_structure.edges if e.is_core]
        peripheral_edges = [e for e in structure.abstract_structure.edges if not e.is_core]
        assert len(core_edges) == 1
        assert len(peripheral_edges) == 1
        assert core_edges[0].source == "X"
        assert peripheral_edges[0].source == "Y"

    def test_is_core_change_detected_in_diff(self):
        """is_core の変更が diff として検出される。"""
        from core.extractor import compute_structure_diff

        base = _make_structure(
            abstract_structure=AbstractStructure(
                variables=["X", "Y"],
                edges=[CausalEdge(source="X", target="Y", is_core=True)],
                smiles_dsl="(x:Agent:X) ==[CAUSES:causes:+]=> (y:Resource:Y)",
            )
        )
        proposed = _make_structure(
            abstract_structure=AbstractStructure(
                variables=["X", "Y"],
                edges=[CausalEdge(source="X", target="Y", is_core=False)],
                smiles_dsl="(x:Agent:X) -[CAUSES:causes:+]-> (y:Resource:Y)",
            )
        )
        diffs = compute_structure_diff(base, proposed)
        paths = {d.field_path for d in diffs}
        assert "abstract_structure.edges" in paths
        assert "abstract_structure.smiles_dsl" in paths
