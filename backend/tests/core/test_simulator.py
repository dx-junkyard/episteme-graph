"""simulator モジュールの単体テスト。

DB・LLM呼び出しをモック化してロジックをテストする。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core.simulator import (
    _compute_kg_diff,
    _extract_concepts_from_kg,
    _extract_relations_from_kg,
    _build_extended_schema_prompt,
)


# ---------------------------------------------------------------------------
# KG解析ヘルパーのテスト
# ---------------------------------------------------------------------------


class TestExtractConceptsFromKG:
    """_extract_concepts_from_kg のテスト。"""

    def test_empty_kg(self):
        concepts = _extract_concepts_from_kg({})
        assert concepts == []

    def test_variables_as_strings(self):
        kg = {
            "abstract_structure": {
                "variables": ["X", "Y", "Z"],
                "edges": [],
            }
        }
        concepts = _extract_concepts_from_kg(kg)
        assert len(concepts) == 3
        assert concepts[0]["name"] == "X"
        assert concepts[0]["type"] == "variable"

    def test_variables_as_dicts(self):
        kg = {
            "abstract_structure": {
                "variables": [
                    {"name": "Stress", "ontology_type": "Event"},
                    {"name": "Cortisol", "ontology_type": "Resource"},
                ],
                "edges": [],
            }
        }
        concepts = _extract_concepts_from_kg(kg)
        assert len(concepts) == 2
        assert concepts[0]["name"] == "Stress"
        assert concepts[0]["type"] == "Event"

    def test_edges_add_unique_concepts(self):
        kg = {
            "abstract_structure": {
                "variables": ["A"],
                "edges": [
                    {"source": "A", "target": "B", "ontology_level": "Agent"},
                    {"source": "C", "target": "A", "ontology_level": "Event"},
                ],
            }
        }
        concepts = _extract_concepts_from_kg(kg)
        names = [c["name"] for c in concepts]
        assert "A" in names
        assert "B" in names
        assert "C" in names
        # A は variables から取得済みなのでエッジからは追加されない
        assert names.count("A") == 1


class TestExtractRelationsFromKG:
    """_extract_relations_from_kg のテスト。"""

    def test_empty_kg(self):
        relations = _extract_relations_from_kg({})
        assert relations == []

    def test_extracts_relations(self):
        kg = {
            "abstract_structure": {
                "edges": [
                    {
                        "source": "A",
                        "target": "B",
                        "core_predicate": "CAUSES",
                        "domain_verb": "triggers",
                        "polarity": "+",
                        "is_core": True,
                    },
                    {
                        "source": "B",
                        "target": "C",
                        "core_predicate": "INHIBITS",
                        "domain_verb": "suppresses",
                        "polarity": "-",
                        "is_core": False,
                    },
                ],
            }
        }
        relations = _extract_relations_from_kg(kg)
        assert len(relations) == 2
        assert relations[0]["predicate"] == "CAUSES"
        assert relations[0]["is_core"] is True
        assert relations[1]["predicate"] == "INHIBITS"


class TestComputeKGDiff:
    """_compute_kg_diff のテスト。"""

    def test_identical_kgs(self):
        kg = {
            "abstract_structure": {
                "variables": ["X"],
                "edges": [{"source": "X", "target": "Y", "core_predicate": "CAUSES"}],
            }
        }
        diff = _compute_kg_diff(kg, kg)
        assert diff["added_concepts"] == []
        assert diff["removed_concepts"] == []
        assert diff["added_relations"] == []
        assert diff["removed_relations"] == []

    def test_added_concepts(self):
        before = {
            "abstract_structure": {
                "variables": ["A"],
                "edges": [],
            }
        }
        after = {
            "abstract_structure": {
                "variables": ["A", "B"],
                "edges": [],
            }
        }
        diff = _compute_kg_diff(before, after)
        assert len(diff["added_concepts"]) == 1
        assert diff["added_concepts"][0]["name"] == "B"
        assert diff["removed_concepts"] == []
        assert diff["after_concept_count"] == 2

    def test_removed_concepts(self):
        before = {
            "abstract_structure": {
                "variables": ["A", "B"],
                "edges": [],
            }
        }
        after = {
            "abstract_structure": {
                "variables": ["A"],
                "edges": [],
            }
        }
        diff = _compute_kg_diff(before, after)
        assert diff["added_concepts"] == []
        assert len(diff["removed_concepts"]) == 1
        assert diff["removed_concepts"][0]["name"] == "B"

    def test_added_and_removed_relations(self):
        before = {
            "abstract_structure": {
                "variables": [],
                "edges": [
                    {"source": "A", "target": "B", "core_predicate": "CAUSES"},
                ],
            }
        }
        after = {
            "abstract_structure": {
                "variables": [],
                "edges": [
                    {"source": "A", "target": "C", "core_predicate": "INHIBITS"},
                ],
            }
        }
        diff = _compute_kg_diff(before, after)
        assert len(diff["added_relations"]) == 1
        assert len(diff["removed_relations"]) == 1
        assert diff["added_relations"][0]["target"] == "C"
        assert diff["removed_relations"][0]["target"] == "B"

    def test_empty_before_and_after(self):
        diff = _compute_kg_diff({}, {})
        assert diff["before_concept_count"] == 0
        assert diff["after_concept_count"] == 0


class TestBuildExtendedSchemaPrompt:
    """_build_extended_schema_prompt のテスト。"""

    @patch("core.simulator.get_ontology_types")
    @patch("core.simulator.get_predicates")
    def test_adds_new_items(self, mock_preds, mock_types):
        mock_types.return_value = [
            {"id": "Agent", "label": "Agent", "description": "行動主体"},
        ]
        mock_preds.return_value = [
            {"id": "CAUSES", "label": "CAUSES", "description": "原因"},
        ]

        proposal_items = [
            {"item_type": "ontology_type", "key": "Experiment", "label": "Experiment", "description": "実験"},
            {"item_type": "predicate", "key": "PROVED_BY", "label": "PROVED_BY", "description": "証明"},
        ]

        types_prompt, preds_prompt = _build_extended_schema_prompt(proposal_items)
        assert "Agent" in types_prompt
        assert "Experiment" in types_prompt
        assert "[NEW]" in types_prompt
        assert "CAUSES" in preds_prompt
        assert "PROVED_BY" in preds_prompt
        assert "[NEW]" in preds_prompt

    @patch("core.simulator.get_ontology_types")
    @patch("core.simulator.get_predicates")
    def test_skips_duplicate_items(self, mock_preds, mock_types):
        mock_types.return_value = [
            {"id": "Agent", "label": "Agent", "description": "行動主体"},
        ]
        mock_preds.return_value = []

        # Agent は既に存在するので追加されない
        proposal_items = [
            {"item_type": "ontology_type", "key": "Agent", "label": "Agent", "description": "行動主体"},
        ]

        types_prompt, _ = _build_extended_schema_prompt(proposal_items)
        assert types_prompt.count("Agent") == 1  # 1回だけ出現


# ---------------------------------------------------------------------------
# run_simulation のテスト
# ---------------------------------------------------------------------------


class TestRunSimulation:
    """run_simulation のテスト（DB・LLMモック）。"""

    @patch("core.simulator.get_proposal_with_items")
    def test_returns_none_for_missing_proposal(self, mock_get):
        mock_get.return_value = None

        from core.simulator import run_simulation
        result = run_simulation("nonexistent")
        assert result is None

    @patch("core.simulator.generate_text")
    @patch("core.simulator._select_control_documents")
    @patch("core.simulator._select_similar_documents")
    @patch("core.simulator._select_target_documents")
    @patch("core.simulator.get_proposal_with_items")
    def test_returns_result_with_empty_docs(
        self, mock_get, mock_target, mock_similar, mock_control, mock_llm,
    ):
        """ドキュメントが0件の場合でも正常な結果を返す。"""
        mock_get.return_value = {
            "proposal_id": "p1",
            "status": "pending",
            "summary": "test proposal",
            "reasoning": "test",
            "source_query_count": 5,
            "items": [
                {"item_type": "ontology_type", "key": "X", "label": "X", "description": "test"},
            ],
        }
        mock_target.return_value = []
        mock_similar.return_value = []
        mock_control.return_value = []

        from core.simulator import run_simulation
        result = run_simulation("p1")

        assert result is not None
        assert result["proposal_id"] == "p1"
        assert result["results"]["target"] == []
        assert result["results"]["similar"] == []
        assert result["results"]["control"] == []
        assert result["stats"]["total_added_concepts"] == 0

    @patch("core.simulator.get_ontology_types")
    @patch("core.simulator.get_predicates")
    @patch("core.simulator.generate_text")
    @patch("core.simulator._select_control_documents")
    @patch("core.simulator._select_similar_documents")
    @patch("core.simulator._select_target_documents")
    @patch("core.simulator.get_proposal_with_items")
    def test_returns_result_with_simulation(
        self, mock_get, mock_target, mock_similar, mock_control,
        mock_llm, mock_preds, mock_types,
    ):
        """ドキュメントがある場合のシミュレーション結果を検証する。"""
        mock_get.return_value = {
            "proposal_id": "p1",
            "status": "pending",
            "summary": "test",
            "reasoning": "test",
            "source_query_count": 3,
            "items": [
                {"item_type": "ontology_type", "key": "Experiment", "label": "Experiment", "description": "実験"},
            ],
        }
        mock_target.return_value = [
            {
                "doc_id": "doc1",
                "title": "Test Paper",
                "filename": "test.pdf",
                "knowledge_graph": {
                    "title": "Test Paper",
                    "domain": "physics",
                    "abstract_structure": {
                        "variables": ["A"],
                        "edges": [],
                        "smiles_dsl": "(a:Agent:A)",
                    },
                },
            },
        ]
        mock_similar.return_value = []
        mock_control.return_value = []
        mock_types.return_value = [{"id": "Agent", "label": "Agent", "description": ""}]
        mock_preds.return_value = [{"id": "CAUSES", "label": "CAUSES", "description": ""}]

        sim_result = json.dumps({
            "added_concepts": [{"name": "LaserExperiment", "type": "Experiment"}],
            "removed_concepts": [],
            "added_relations": [],
            "removed_relations": [],
            "reclassified_nodes": [],
            "summary": "実験カテゴリが追加される",
        })
        mock_llm.return_value = sim_result

        from core.simulator import run_simulation
        result = run_simulation("p1")

        assert result is not None
        assert len(result["results"]["target"]) == 1
        assert result["results"]["target"][0]["doc_id"] == "doc1"
        assert len(result["results"]["target"][0]["added_concepts"]) == 1
        assert result["stats"]["total_added_concepts"] == 1
        assert result["stats"]["target_doc_count"] == 1


# ---------------------------------------------------------------------------
# approve_with_scope のテスト
# ---------------------------------------------------------------------------


class TestApproveWithScope:
    """approve_with_scope のテスト。"""

    @patch("core.reextractor.enqueue_reextraction")
    @patch("core.meta_analyzer.approve_proposal")
    def test_full_scope_approval(self, mock_approve, mock_enqueue):
        mock_approve.return_value = True
        mock_enqueue.return_value = {
            "job_id": "j1",
            "proposal_id": "p1",
            "status": "pending",
            "total_docs": 3,
            "processed_docs": 0,
        }

        from core.simulator import approve_with_scope
        result = approve_with_scope("p1", "user1", scope="full")

        assert result is not None
        assert result["scope"] == "full"
        assert result["status"] == "approved"
        mock_approve.assert_called_once_with("p1", "user1")
        mock_enqueue.assert_called_once_with("p1")

    @patch("core.meta_analyzer.approve_proposal")
    def test_returns_none_for_failed_approval(self, mock_approve):
        mock_approve.return_value = False

        from core.simulator import approve_with_scope
        result = approve_with_scope("nonexistent", "user1")
        assert result is None
