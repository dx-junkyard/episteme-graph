"""学習チャットのグラフ要素説明ポップアップ（Phase 2 §5.3, hierarchical_context_explanation_design.md）
への element_explanations 配線の回帰テスト。

対象: ``backend/api/routes/learning.py``
  - ``_element_explanation_ref_for_graph_context``: グラフ要素ポップアップの
    (element_type, element_id) を element_explanations のポリモーフィック語彙へマップする
    純関数。
  - ``_approved_graph_element_answer``: 承認済み(approved) element_explanations があれば
    それを主文にした回答を組み立てる。
  - ``_generate_graph_element_explanation``: 優先順位 = approved contextual →
    （既存の）ローカル生成、を満たすこと。承認済みがあればローカル LLM 生成をスキップする。

DB は一切呼び出さない（``element_explanations.approved_for_elements`` /
``_resolve_course_document_ids`` / ``get_graph_element_context`` 等の境界のみモックする。
``test_learner_figure_delivery.py`` と同型の手法）。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


# ---------------------------------------------------------------------------
# _element_explanation_ref_for_graph_context: element_type マッピング
# ---------------------------------------------------------------------------


class TestElementExplanationRefMapping:
    def test_formula_with_target_formula_maps_to_equation(self):
        from api.routes.learning import _element_explanation_ref_for_graph_context
        from core import element_explanations

        context = {"element_type": "formula", "target_formula": {"id": "eq_2_7", "latex": "E=mc^2"}}
        assert _element_explanation_ref_for_graph_context(context) == (
            element_explanations.ELEMENT_TYPE_EQUATION, "eq_2_7",
        )

    def test_concept_has_no_mapping(self):
        """legacy な document.knowledge_graph 由来の concept は theory_components と
        ID 体系が異なるため対応不能（誤結合を避けるためマップしない）。"""
        from api.routes.learning import _element_explanation_ref_for_graph_context

        context = {"element_type": "concept", "element_id": "heavy_quark_limit"}
        assert _element_explanation_ref_for_graph_context(context) is None

    def test_relationship_has_no_mapping(self):
        from api.routes.learning import _element_explanation_ref_for_graph_context

        context = {"element_type": "relationship", "element_id": "a:RELATED_TO:b"}
        assert _element_explanation_ref_for_graph_context(context) is None

    def test_citation_has_no_mapping(self):
        from api.routes.learning import _element_explanation_ref_for_graph_context

        context = {"element_type": "citation", "element_id": "bib:Smith2020"}
        assert _element_explanation_ref_for_graph_context(context) is None

    def test_reference_has_no_mapping(self):
        from api.routes.learning import _element_explanation_ref_for_graph_context

        context = {"element_type": "reference", "element_id": "ref:eq:main"}
        assert _element_explanation_ref_for_graph_context(context) is None

    def test_component_maps_to_theory_component(self):
        from api.routes.learning import _element_explanation_ref_for_graph_context
        from core import element_explanations

        context = {"element_type": "component", "element_id": "comp-1"}
        assert _element_explanation_ref_for_graph_context(context) == (
            element_explanations.ELEMENT_TYPE_COMPONENT, "comp-1",
        )

    def test_claim_maps_to_theory_claim(self):
        from api.routes.learning import _element_explanation_ref_for_graph_context
        from core import element_explanations

        context = {"element_type": "claim", "element_id": "claim-1"}
        assert _element_explanation_ref_for_graph_context(context) == (
            element_explanations.ELEMENT_TYPE_CLAIM, "claim-1",
        )

    def test_no_element_type_and_no_target_formula_has_no_mapping(self):
        from api.routes.learning import _element_explanation_ref_for_graph_context

        assert _element_explanation_ref_for_graph_context({}) is None


# ---------------------------------------------------------------------------
# _approved_graph_element_answer
# ---------------------------------------------------------------------------


class TestApprovedGraphElementAnswer:
    @patch("core.element_explanations.approved_for_elements")
    @patch("api.routes.learning._resolve_course_document_ids")
    def test_contextual_and_generic_are_combined_with_citation_marker(
        self, mock_resolve, mock_approved,
    ):
        from api.routes.learning import _approved_graph_element_answer
        from core import element_explanations

        mock_resolve.return_value = ["doc-1"]
        ref = (element_explanations.ELEMENT_TYPE_EQUATION, "eq_1")
        mock_approved.return_value = {
            ref: [
                {
                    "kind": element_explanations.KIND_CONTEXTUAL,
                    "body": "この式はゼロ反跳極限での崩壊率を与える。",
                    "status": element_explanations.STATUS_APPROVED,
                    # confidence の生値が evidence に残っていても body 以外は使わない
                    "evidence": {"confidence": 0.93, "reason": "..."},
                },
                {
                    "kind": element_explanations.KIND_GENERIC,
                    "body": "崩壊率とは単位時間あたりの遷移確率である。",
                    "status": element_explanations.STATUS_APPROVED,
                    "evidence": {"confidence": 0.81},
                },
            ],
        }

        context = {"element_type": "formula", "target_formula": {"id": "eq_1"}, "material_id": "mat-1"}
        answer = _approved_graph_element_answer(context, "この数式", "論文タイトル")

        assert answer is not None
        assert "この式はゼロ反跳極限での崩壊率を与える。" in answer
        assert "一般には、崩壊率とは単位時間あたりの遷移確率である。" in answer
        assert "[出典: 『論文タイトル』]" in answer
        # confidence の生値・status・kind といった内部フィールドは文面に一切出てこない
        assert "0.93" not in answer
        assert "0.81" not in answer
        assert "confidence" not in answer
        assert "candidate" not in answer
        assert "approved" not in answer

    @patch("core.element_explanations.approved_for_elements")
    @patch("api.routes.learning._resolve_course_document_ids")
    def test_contextual_only_omits_generic_paragraph(self, mock_resolve, mock_approved):
        from api.routes.learning import _approved_graph_element_answer
        from core import element_explanations

        mock_resolve.return_value = ["doc-1"]
        ref = (element_explanations.ELEMENT_TYPE_EQUATION, "eq_1")
        mock_approved.return_value = {
            ref: [{
                "kind": element_explanations.KIND_CONTEXTUAL,
                "body": "文脈説明のみ。",
                "status": element_explanations.STATUS_APPROVED,
            }],
        }

        context = {"element_type": "formula", "target_formula": {"id": "eq_1"}, "material_id": "mat-1"}
        answer = _approved_graph_element_answer(context, "この数式", "論文タイトル")

        assert "文脈説明のみ。" in answer
        assert "一般には、" not in answer

    @patch("core.element_explanations.approved_for_elements")
    @patch("api.routes.learning._resolve_course_document_ids")
    def test_no_approved_rows_returns_none(self, mock_resolve, mock_approved):
        from api.routes.learning import _approved_graph_element_answer

        mock_resolve.return_value = ["doc-1"]
        mock_approved.return_value = {}

        context = {"element_type": "formula", "target_formula": {"id": "eq_1"}, "material_id": "mat-1"}
        assert _approved_graph_element_answer(context, "label", "title") is None

    @patch("api.routes.learning._resolve_course_document_ids")
    def test_unmappable_element_type_returns_none_without_db_lookup(self, mock_resolve):
        from api.routes.learning import _approved_graph_element_answer

        context = {"element_type": "concept", "element_id": "c1", "material_id": "mat-1"}
        assert _approved_graph_element_answer(context, "label", "title") is None
        mock_resolve.assert_not_called()

    def test_missing_material_id_returns_none(self):
        from api.routes.learning import _approved_graph_element_answer

        context = {"element_type": "formula", "target_formula": {"id": "eq_1"}, "material_id": ""}
        assert _approved_graph_element_answer(context, "label", "title") is None

    @patch("core.element_explanations.approved_for_elements", side_effect=RuntimeError("db down"))
    @patch("api.routes.learning._resolve_course_document_ids")
    def test_db_failure_is_fail_soft_returns_none(self, mock_resolve, _mock_approved):
        from api.routes.learning import _approved_graph_element_answer

        mock_resolve.return_value = ["doc-1"]
        context = {"element_type": "formula", "target_formula": {"id": "eq_1"}, "material_id": "mat-1"}
        assert _approved_graph_element_answer(context, "label", "title") is None


# ---------------------------------------------------------------------------
# _generate_graph_element_explanation: 優先順位の統合テスト
# ---------------------------------------------------------------------------


class TestGenerateGraphElementExplanationPriority:
    @patch("api.routes.learning.persist_chat_history")
    @patch("api.routes.learning.record_student_stumble_event")
    @patch("core.element_explanations.approved_for_elements")
    @patch("api.routes.learning._resolve_course_document_ids")
    @patch("api.routes.learning.get_graph_element_context")
    @patch("api.routes.learning.generate_text")
    def test_approved_explanation_short_circuits_local_generation(
        self,
        mock_generate_text,
        mock_context,
        mock_resolve,
        mock_approved,
        _mock_stumble,
        mock_persist,
    ):
        from api.routes.learning import _generate_graph_element_explanation
        from core import element_explanations
        from schemas import LearningChatRequest

        mock_context.return_value = {
            "chunk_id": "chunk-1",
            "chunk_text": "本文",
            "formulas": [],
            "material_id": "mat-1",
            "source_title": "論文タイトル",
            "instructor_id": None,
            "element_id": "eq_1",
            "element_type": "formula",
            "element_label": "この数式",
            "target_formula": {"id": "eq_1", "latex": "E=mc^2", "is_display": True},
            "target_citation": None,
            "target_reference": None,
            "graph_description": "",
            "related_chunks": [],
        }
        mock_resolve.return_value = ["doc-1"]
        ref = (element_explanations.ELEMENT_TYPE_EQUATION, "eq_1")
        mock_approved.return_value = {
            ref: [{
                "kind": element_explanations.KIND_CONTEXTUAL,
                "body": "承認済みの文脈説明。",
                "status": element_explanations.STATUS_APPROVED,
            }],
        }

        body = LearningChatRequest(
            message="", chunk_id="chunk-1", element_id="eq_1", element_type="formula",
        )
        resp = _generate_graph_element_explanation(
            user_id="u1", course_id="c1", topic_id="t1",
            course_title="コース", topic_title="トピック",
            course_data={}, body=body,
        )

        assert "承認済みの文脈説明。" in resp.answer
        mock_generate_text.assert_not_called()
        mock_persist.assert_called_once()

    @patch("api.routes.learning.persist_chat_history")
    @patch("api.routes.learning.record_student_stumble_event")
    @patch("api.routes.learning._resolve_course_document_ids")
    @patch("api.routes.learning.get_graph_element_context")
    @patch("api.routes.learning.generate_text")
    def test_no_approved_explanation_falls_back_to_existing_graph_description(
        self, mock_generate_text, mock_context, mock_resolve, _mock_stumble, mock_persist,
    ):
        """approved が無い場合は既存のローカル生成（ここでは graph_description 経路）へ
        縮退する。element_type が concept のため element_explanations の参照すら試みない。"""
        from api.routes.learning import _generate_graph_element_explanation
        from schemas import LearningChatRequest

        mock_context.return_value = {
            "chunk_id": "chunk-1",
            "chunk_text": "本文",
            "formulas": [],
            "material_id": "mat-1",
            "source_title": "論文タイトル",
            "instructor_id": None,
            "element_id": "heavy_quark_limit",
            "element_type": "concept",
            "element_label": "重いクォーク極限",
            "target_formula": None,
            "target_citation": None,
            "target_reference": None,
            "graph_description": "重いクォーク質量を無限大に近づける近似。",
            "related_chunks": [],
        }

        body = LearningChatRequest(
            message="", chunk_id="chunk-1", element_id="heavy_quark_limit", element_type="concept",
        )
        resp = _generate_graph_element_explanation(
            user_id="u1", course_id="c1", topic_id="t1",
            course_title="コース", topic_title="トピック",
            course_data={}, body=body,
        )

        assert "重いクォーク質量を無限大に近づける近似。" in resp.answer
        mock_generate_text.assert_not_called()
        mock_resolve.assert_not_called()
        mock_persist.assert_called_once()
