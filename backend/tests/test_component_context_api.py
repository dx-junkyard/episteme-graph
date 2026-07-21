"""``GET /api/learning/courses/{course_id}/components/{component_id}/context`` の
fail-closed ゲート・レスポンス組み立てのテスト（component_evidence_redesign Phase 2/3）。

``test_learner_figure_delivery.py`` の ``TestGetCourseFigureImageGating``
（``@patch("api.routes.learning.X")`` で境界だけをモックする方針）を踏襲する。
DB/実 core ロジックは呼ばない — 境界（``get_accessible_course_data`` /
``_course_document_ids`` / ``build_component_context`` /
``_first_approved_component_explanation``）のみモックし、ルート関数自体の
ゲート判定・レスポンス合成ロジックを検証する。
"""

from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import patch

import pytest

_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


def _course_data():
    return {"topics": [], "sources": [{"material_id": "mat-1"}]}


class TestGetCourseComponentContextGating:
    @patch("api.routes.learning.get_accessible_course_data", return_value=None)
    def test_inaccessible_course_returns_404(self, _mock_course):
        from fastapi import HTTPException

        from api.routes.learning import get_course_component_context

        with pytest.raises(HTTPException) as exc:
            get_course_component_context("c1", "comp-1", {"id": "u1"})
        assert exc.value.status_code == 404
        assert exc.value.detail == "Course not found"

    @patch("api.routes.learning.build_component_context", return_value=None)
    @patch("api.routes.learning._course_document_ids", return_value=["doc-1"])
    @patch("api.routes.learning.get_accessible_course_data")
    def test_core_returns_none_yields_404(self, mock_course, _mock_doc_ids, _mock_build):
        """component が解決できない(コース外 document / 未知の component_id)場合は
        core が None を返し、ルートは 404 "Component not found" にマッピングする。"""
        from fastapi import HTTPException

        from api.routes.learning import get_course_component_context

        mock_course.return_value = _course_data()
        with pytest.raises(HTTPException) as exc:
            get_course_component_context("c1", "comp-1", {"id": "u1"})
        assert exc.value.status_code == 404
        assert exc.value.detail == "Component not found"

    @patch("api.routes.learning._first_approved_component_explanation", return_value=None)
    @patch("api.routes.learning.build_component_context")
    @patch("api.routes.learning._course_document_ids", return_value=["doc-1"])
    @patch("api.routes.learning.get_accessible_course_data")
    def test_returns_core_result_without_explanation_when_none_approved(
        self, mock_course, _mock_doc_ids, mock_build, _mock_expl,
    ):
        from api.routes.learning import get_course_component_context

        mock_course.return_value = _course_data()
        mock_build.return_value = {
            "component_id": "comp-uuid-1",
            "instance": {"component": {"label": "X"}},
            "shared_part": None,
            "graph": None,
        }

        result = get_course_component_context("c1", "comp-1", {"id": "u1"})

        assert result["component_id"] == "comp-uuid-1"
        assert "explanation" not in result["instance"]

    @patch("api.routes.learning._first_approved_component_explanation")
    @patch("api.routes.learning.build_component_context")
    @patch("api.routes.learning._course_document_ids", return_value=["doc-1"])
    @patch("api.routes.learning.get_accessible_course_data")
    def test_approved_explanation_is_merged_into_instance(
        self, mock_course, _mock_doc_ids, mock_build, mock_expl,
    ):
        from api.routes.learning import get_course_component_context

        mock_course.return_value = _course_data()
        mock_build.return_value = {
            "component_id": "comp-uuid-1",
            "instance": {"component": {"label": "X"}},
            "shared_part": None,
            "graph": None,
        }
        mock_expl.return_value = {
            "kind": "standard",
            "title": "標準説明",
            "body": "本文",
            "author_name": "先生A",
            "endorsement_label": "3名の教員が承認",
        }

        result = get_course_component_context("c1", "comp-1", {"id": "u1"})

        assert result["instance"]["explanation"] == mock_expl.return_value
        # explanation 検索は core の戻り値の component_id(DB UUID)で行う(path の
        # component_id が agent 側 ID であっても正しい UUID で引けるようにするため)。
        mock_expl.assert_called_once_with("comp-uuid-1", "c1")

    @patch("api.routes.learning.build_component_context")
    @patch("api.routes.learning._course_document_ids")
    @patch("api.routes.learning.get_accessible_course_data")
    def test_course_document_ids_derived_from_accessible_course_data(
        self, mock_course, mock_doc_ids, mock_build,
    ):
        """コース document 集合は受講ゲート済みの course_data から
        ``_course_document_ids`` で導出し、そのまま core へ渡す(図配信 API と同じ
        受講ゲートの一貫性)。"""
        from fastapi import HTTPException

        from api.routes.learning import get_course_component_context

        course_data = _course_data()
        mock_course.return_value = course_data
        mock_doc_ids.return_value = ["doc-a", "doc-b"]
        mock_build.return_value = None

        with pytest.raises(HTTPException):
            get_course_component_context("c1", "comp-1", {"id": "u1"})

        mock_doc_ids.assert_called_once_with(course_data)
        mock_build.assert_called_once_with("comp-1", "c1", {"doc-a", "doc-b"})


class TestFirstApprovedComponentExplanationQueryShape:
    def test_query_restricts_to_teacher_approved_course_scope_and_single_row(self):
        """SQL 文字列に承認条件(review_status='teacher_approved')・course_id
        スコープ・単一行制限が含まれることを静的に確認する(実 DB を使わない軽量な回帰)。"""
        from api.routes.learning import _first_approved_component_explanation

        source = inspect.getsource(_first_approved_component_explanation)
        assert "review_status = 'teacher_approved'" in source
        assert "e.course_id = :course_id" in source
        assert "LIMIT 1" in source
