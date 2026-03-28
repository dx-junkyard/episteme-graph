"""simulator モジュールの単体テスト。

DB・LLM呼び出しをモック化してシミュレーションロジックをテストする。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core.simulator import (
    _build_extended_schema_prompt,
    _simulate_extraction_for_doc,
    run_simulation,
)


class TestBuildExtendedSchemaPrompt:
    """拡張スキーマプロンプト構築のテスト。"""

    @patch("core.simulator.get_predicates")
    @patch("core.simulator.get_ontology_types")
    def test_includes_existing_and_new_types(self, mock_types, mock_preds):
        mock_types.return_value = [
            {"id": "Agent", "label": "Agent", "description": "エージェント"},
        ]
        mock_preds.return_value = [
            {"id": "CAUSES", "label": "CAUSES", "description": "因果関係"},
        ]

        items = [
            {"item_type": "ontology_type", "key": "Experiment", "label": "Experiment", "description": "実験"},
            {"item_type": "predicate", "key": "PROVED_BY", "label": "PROVED_BY", "description": "証明"},
        ]
        prompt = _build_extended_schema_prompt(items)

        assert "Agent" in prompt
        assert "Experiment" in prompt
        assert "[NEW]" in prompt
        assert "CAUSES" in prompt
        assert "PROVED_BY" in prompt

    @patch("core.simulator.get_predicates")
    @patch("core.simulator.get_ontology_types")
    def test_empty_proposal_items(self, mock_types, mock_preds):
        mock_types.return_value = [
            {"id": "Agent", "label": "Agent", "description": "エージェント"},
        ]
        mock_preds.return_value = []

        prompt = _build_extended_schema_prompt([])
        assert "Agent" in prompt
        assert "[NEW]" not in prompt


class TestSimulateExtractionForDoc:
    """1ドキュメント単位のシミュレーションテスト。"""

    @patch("core.simulator.generate_text")
    def test_successful_simulation(self, mock_llm):
        mock_llm.return_value = json.dumps({
            "added_concepts": [
                {"id": "exp1", "name": "散乱実験", "type": "Experiment", "reason": "新カテゴリ適用"},
            ],
            "removed_concepts": [],
            "reclassified_concepts": [
                {"id": "c1", "name": "散乱", "old_type": "Event", "new_type": "Experiment", "reason": "再分類"},
            ],
            "added_relationships": [
                {"source": "exp1", "target": "c2", "relation": "PROVED_BY", "reason": "新関係"},
            ],
            "removed_relationships": [],
            "summary": "実験カテゴリ適用で1概念追加、1概念再分類",
        })

        doc = {
            "doc_id": "doc-001",
            "title": "テスト論文",
            "knowledge_graph": {
                "concepts": [
                    {"id": "c1", "name": "散乱", "type": "Event"},
                    {"id": "c2", "name": "光子", "type": "Particle"},
                ],
                "relationships": [
                    {"source": "c1", "target": "c2", "relation": "CONTAINS"},
                ],
            },
        }
        items = [
            {"item_type": "ontology_type", "key": "Experiment", "label": "Experiment", "description": "実験"},
        ]

        result = _simulate_extraction_for_doc(doc, items, "test prompt")

        assert result["doc_id"] == "doc-001"
        assert result["before"]["concept_count"] == 2
        assert result["before"]["relationship_count"] == 1
        # After: 2 existing + 1 added - 0 removed = 3
        assert result["after"]["concept_count"] == 3
        # After: 1 existing + 1 added - 0 removed = 2
        assert result["after"]["relationship_count"] == 2
        assert len(result["diff"]["added_concepts"]) == 1
        assert len(result["diff"]["reclassified_concepts"]) == 1

    @patch("core.simulator.generate_text")
    def test_llm_failure_returns_empty_diff(self, mock_llm):
        mock_llm.side_effect = Exception("LLM API error")

        doc = {
            "doc_id": "doc-002",
            "title": "失敗テスト",
            "knowledge_graph": {"concepts": [], "relationships": []},
        }

        result = _simulate_extraction_for_doc(doc, [], "test prompt")

        assert result["doc_id"] == "doc-002"
        assert result["diff"]["added_concepts"] == []
        assert "失敗" in result["diff"]["summary"]

    @patch("core.simulator.generate_text")
    def test_no_changes_needed(self, mock_llm):
        mock_llm.return_value = json.dumps({
            "added_concepts": [],
            "removed_concepts": [],
            "reclassified_concepts": [],
            "added_relationships": [],
            "removed_relationships": [],
            "summary": "変更不要",
        })

        doc = {
            "doc_id": "doc-003",
            "title": "変更なし論文",
            "knowledge_graph": {
                "concepts": [{"id": "c1", "name": "X"}],
                "relationships": [],
            },
        }

        result = _simulate_extraction_for_doc(doc, [], "test prompt")

        assert result["before"]["concept_count"] == 1
        assert result["after"]["concept_count"] == 1
        assert result["diff"]["summary"] == "変更不要"


class TestRunSimulation:
    """run_simulation 統合テスト。"""

    @patch("core.simulator._select_control_docs")
    @patch("core.simulator._select_similar_docs")
    @patch("core.simulator._select_target_docs")
    @patch("core.simulator._get_proposal_info")
    @patch("core.simulator.generate_text")
    @patch("core.simulator.get_predicates")
    @patch("core.simulator.get_ontology_types")
    def test_full_simulation_flow(
        self, mock_types, mock_preds, mock_llm,
        mock_proposal, mock_target, mock_similar, mock_control,
    ):
        mock_types.return_value = [{"id": "Agent", "label": "Agent", "description": ""}]
        mock_preds.return_value = [{"id": "CAUSES", "label": "CAUSES", "description": ""}]

        mock_proposal.return_value = {
            "proposal_id": "prop-001",
            "status": "pending",
            "summary": "テスト提案",
            "reasoning": "テスト",
            "source_query_count": 5,
            "items": [
                {"item_type": "ontology_type", "key": "Experiment", "label": "Experiment", "description": "実験"},
            ],
        }

        mock_target.return_value = [
            {"doc_id": "d1", "title": "Target Doc", "knowledge_graph": {"concepts": [{"id": "c1"}], "relationships": []}},
        ]
        mock_similar.return_value = [
            {"doc_id": "d2", "title": "Similar Doc", "knowledge_graph": {"concepts": [], "relationships": []}},
        ]
        mock_control.return_value = [
            {"doc_id": "d3", "title": "Control Doc", "knowledge_graph": {"concepts": [], "relationships": []}},
        ]

        mock_llm.return_value = json.dumps({
            "added_concepts": [],
            "removed_concepts": [],
            "reclassified_concepts": [],
            "added_relationships": [],
            "removed_relationships": [],
            "summary": "変更なし",
        })

        result = run_simulation("prop-001")

        assert result["proposal_id"] == "prop-001"
        assert len(result["target_docs"]) == 1
        assert len(result["similar_docs"]) == 1
        assert len(result["control_docs"]) == 1
        assert "3件" in result["overall_summary"]

    @patch("core.simulator._get_proposal_info")
    def test_proposal_not_found(self, mock_proposal):
        mock_proposal.return_value = None

        with pytest.raises(ValueError, match="Proposal not found"):
            run_simulation("nonexistent")

    @patch("core.simulator._get_proposal_info")
    def test_empty_proposal_items(self, mock_proposal):
        mock_proposal.return_value = {
            "proposal_id": "prop-empty",
            "status": "pending",
            "summary": "空の提案",
            "reasoning": "",
            "source_query_count": 0,
            "items": [],
        }

        result = run_simulation("prop-empty")

        assert result["proposal_id"] == "prop-empty"
        assert result["target_docs"] == []
        assert "シミュレーション不要" in result["overall_summary"]

    @patch("core.simulator._select_control_docs")
    @patch("core.simulator._select_similar_docs")
    @patch("core.simulator._select_target_docs")
    @patch("core.simulator._get_proposal_info")
    @patch("core.simulator.generate_text")
    @patch("core.simulator.get_predicates")
    @patch("core.simulator.get_ontology_types")
    def test_overfitting_warning(
        self, mock_types, mock_preds, mock_llm,
        mock_proposal, mock_target, mock_similar, mock_control,
    ):
        """Control群の変化がTarget群以上の場合、過剰適合警告が出る。"""
        mock_types.return_value = []
        mock_preds.return_value = []

        mock_proposal.return_value = {
            "proposal_id": "prop-over",
            "status": "pending",
            "summary": "過剰適合テスト",
            "reasoning": "",
            "source_query_count": 5,
            "items": [
                {"item_type": "ontology_type", "key": "X", "label": "X", "description": "X"},
            ],
        }

        mock_target.return_value = [
            {"doc_id": "t1", "title": "Target", "knowledge_graph": {"concepts": [{"id": "c1"}], "relationships": []}},
        ]
        mock_similar.return_value = []
        mock_control.return_value = [
            {"doc_id": "ctrl1", "title": "Control", "knowledge_graph": {"concepts": [{"id": "c2"}], "relationships": []}},
        ]

        call_count = [0]

        def mock_llm_responses(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Target: 1 added concept
                return json.dumps({
                    "added_concepts": [{"id": "new1", "name": "New1", "type": "X", "reason": ""}],
                    "removed_concepts": [],
                    "reclassified_concepts": [],
                    "added_relationships": [],
                    "removed_relationships": [],
                    "summary": "",
                })
            else:
                # Control: 2 added concepts (more than target)
                return json.dumps({
                    "added_concepts": [
                        {"id": "new2", "name": "New2", "type": "X", "reason": ""},
                        {"id": "new3", "name": "New3", "type": "X", "reason": ""},
                    ],
                    "removed_concepts": [],
                    "reclassified_concepts": [],
                    "added_relationships": [],
                    "removed_relationships": [],
                    "summary": "",
                })

        mock_llm.side_effect = mock_llm_responses

        result = run_simulation("prop-over")

        assert "過剰適合" in result["overall_summary"]
        assert "カナリアリリース" in result["overall_summary"]
