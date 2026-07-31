"""``GET /api/learning/courses/{course_id}/elements/{element_type}/{element_id}/context`` の
fail-closed ゲート・レスポンス契約のテスト（learner_element_context_design Phase 3）。

``test_component_context_api.py`` の方針（``@patch("api.routes.learning.X")`` で境界だけを
モックし、ルート関数自体のゲート判定・レスポンス合成を検証する）を踏襲する。
DB / 実 core ロジックは呼ばない。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


def _course_data():
    return {"topics": [], "sources": [{"material_id": "mat-1"}]}


def _context_payload():
    return {
        "available": True,
        "element_type": "claim",
        "element_id": "claim-uuid-1",
        "focus": {
            "element_type": "claim",
            "element_id": "claim-uuid-1",
            "document_id": "doc-1",
            "label": "主張ラベル",
            "intrinsic_summary": "主張の本文",
            "contextual_role": "中心命題を支持する",
            "contextual_role_status": "source_backed",
        },
        "upper": [
            {
                "id": "comp-1",
                "element_type": "theory_component",
                "label": "上位コンポーネント",
                "relation_label": "の根拠となる",
                "relation_status": "source_backed",
                "navigable": True,
            }
        ],
        "lower": [],
        "notes": [],
        "provenance": "course_freeze",
    }


class TestGetCourseElementContextGating:
    def test_unknown_element_type_returns_404_before_touching_course(self):
        """未対応の element_type は受講ゲートより手前で 404（学習者 API の 404 統一方針）。"""
        from fastapi import HTTPException

        from api.routes.learning import get_course_element_context

        with patch("api.routes.learning.get_accessible_course_data") as mock_course:
            for element_type in ("figure", "component", "theory_claim", "shared_part", ""):
                with pytest.raises(HTTPException) as exc:
                    get_course_element_context("c1", element_type, "x1", {"id": "u1"})
                assert exc.value.status_code == 404
                assert exc.value.detail == "Element not found"
            mock_course.assert_not_called()

    @patch("api.routes.learning.get_accessible_course_data", return_value=None)
    def test_inaccessible_course_returns_404(self, _mock_course):
        from fastapi import HTTPException

        from api.routes.learning import get_course_element_context

        with pytest.raises(HTTPException) as exc:
            get_course_element_context("c1", "claim", "claim-1", {"id": "u1"})
        assert exc.value.status_code == 404
        assert exc.value.detail == "Course not found"

    @patch("api.routes.learning.build_element_context", return_value=None)
    @patch("api.routes.learning._course_document_ids", return_value=["doc-1"])
    @patch("api.routes.learning.get_accessible_course_data")
    def test_core_returns_none_yields_404(self, mock_course, _mock_doc_ids, _mock_build):
        """要素が解決できない（コース外 document / 未知の element_id）場合は core が
        None を返し、ルートは 404 にマッピングする（fail-closed）。"""
        from fastapi import HTTPException

        from api.routes.learning import get_course_element_context

        mock_course.return_value = _course_data()
        with pytest.raises(HTTPException) as exc:
            get_course_element_context("c1", "claim", "claim-1", {"id": "u1"})
        assert exc.value.status_code == 404
        assert exc.value.detail == "Element not found"

    @patch("api.routes.learning.build_element_context")
    @patch("api.routes.learning._course_document_ids")
    @patch("api.routes.learning.get_accessible_course_data")
    def test_course_document_ids_derived_from_accessible_course_data(
        self, mock_course, mock_doc_ids, mock_build,
    ):
        """コース document 集合は受講ゲート済みの course_data から ``_course_document_ids``
        で導出し、そのまま core へ渡す（component 文脈 API・図配信 API と同じ受講ゲート）。"""
        from fastapi import HTTPException

        from api.routes.learning import get_course_element_context

        course_data = _course_data()
        mock_course.return_value = course_data
        mock_doc_ids.return_value = ["doc-a", "doc-b"]
        mock_build.return_value = None

        with pytest.raises(HTTPException):
            get_course_element_context("c1", "equation", "eq_1", {"id": "u1"})

        mock_doc_ids.assert_called_once_with(course_data)
        mock_build.assert_called_once_with("equation", "eq_1", {"doc-a", "doc-b"})


class TestGetCourseElementContextResponse:
    @patch("api.routes.learning.build_element_context")
    @patch("api.routes.learning._course_document_ids", return_value=["doc-1"])
    @patch("api.routes.learning.get_accessible_course_data")
    def test_returns_core_payload_verbatim(self, mock_course, _mock_doc_ids, mock_build):
        from api.routes.learning import get_course_element_context

        mock_course.return_value = _course_data()
        mock_build.return_value = _context_payload()

        result = get_course_element_context("c1", "claim", "claim_span_7", {"id": "u1"})

        assert result["available"] is True
        assert result["element_type"] == "claim"
        assert result["element_id"] == "claim-uuid-1"
        assert result["provenance"] == "course_freeze"
        assert set(result) == {
            "available", "element_type", "element_id", "focus", "upper", "lower",
            "notes", "provenance",
        }

    @patch("api.routes.learning.build_element_context")
    @patch("api.routes.learning._course_document_ids", return_value=["doc-1"])
    @patch("api.routes.learning.get_accessible_course_data")
    def test_available_false_degradation_is_returned_as_200(
        self, mock_course, _mock_doc_ids, mock_build,
    ):
        """要素は解決できたが文脈投影が得られない場合は 200 + available:false
        （文脈が無いことは異常ではない — fail-soft）。"""
        from api.routes.learning import get_course_element_context

        mock_course.return_value = _course_data()
        mock_build.return_value = {"available": False, "note": "この要素の文脈情報は現在表示できません。"}

        result = get_course_element_context("c1", "equation", "eq_1", {"id": "u1"})

        assert result["available"] is False
        assert result["note"]

    @patch("api.routes.learning._course_document_ids", return_value=["doc-1"])
    @patch("api.routes.learning.get_accessible_course_data")
    def test_no_candidate_or_confidence_in_response_for_real_core(
        self, mock_course, _mock_doc_ids, monkeypatch,
    ):
        """実 core（``build_element_context``）を context_lens だけ差し替えて通し、
        candidate 関係と confidence 生数値がレスポンスに現れないことを確認する。"""
        from core import element_context

        from api.routes.learning import get_course_element_context

        mock_course.return_value = _course_data()
        monkeypatch.setattr(element_context, "document_run_artifacts", lambda doc: {})
        monkeypatch.setattr(
            element_context, "equation_records", lambda doc, artifacts=None: [{"equation_id": "eq_1"}]
        )
        monkeypatch.setattr(
            element_context.context_lens_mod,
            "build",
            lambda ref: {
                "focus": {
                    "label": "E=mc^2",
                    "intrinsic_summary": "E=mc^2",
                    "contextual_role": "AI が推測した役割",
                    "contextual_role_status": "candidate",
                    "provenance": ["equation_semantics:eq_1"],
                    "generic": {"name": "共通部品X", "confidence": 0.77},
                },
                "upper": [
                    {
                        "element_type": "theory_claim", "element_id": "c1", "document_id": "doc-1",
                        "label": "確定した上位", "relation": "quantifies",
                        "relation_label": "を定量化する", "relation_status": "source_backed",
                        "evidence_refs": ["ev_1"], "navigable": True, "confidence": 0.5,
                    },
                    {
                        "element_type": "theory_claim", "element_id": "c2", "document_id": "doc-1",
                        "label": "AI 候補の上位", "relation": "quantifies",
                        "relation_label": "を定量化する", "relation_status": "candidate",
                        "evidence_refs": [], "navigable": True,
                    },
                ],
                "lower": [],
                "notes": [],
            },
        )

        result = get_course_element_context("c1", "equation", "eq_1", {"id": "u1"})

        assert [i["label"] for i in result["upper"]] == ["確定した上位"]
        assert "contextual_role" not in result["focus"]

        def _walk(value):
            if isinstance(value, dict):
                assert "confidence" not in value
                assert value.get("relation_status") != "candidate"
                assert "evidence_refs" not in value
                for v in value.values():
                    _walk(v)
            elif isinstance(value, list):
                for v in value:
                    _walk(v)

        _walk(result)

    @patch("api.routes.learning._course_document_ids", return_value=["doc-1"])
    @patch("api.routes.learning.get_accessible_course_data")
    def test_no_bare_internal_ids_in_response_for_real_core(
        self, mock_course, _mock_doc_ids, monkeypatch,
    ):
        """W層がラベルを引けなかった項目（図の DB UUID / evidence_id / synth claim ID）を
        そのまま返しても、学習者向けレスポンスには裸の内部 ID が出ない（LE4）。
        役割文に内部 ID が混ざる場合は role キーごと落ちる。"""
        from core import element_context

        from api.routes.learning import get_course_element_context

        figure_uuid = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        mock_course.return_value = _course_data()
        monkeypatch.setattr(element_context, "document_run_artifacts", lambda doc: {})
        monkeypatch.setattr(
            element_context, "equation_records", lambda doc, artifacts=None: [{"equation_id": "eq_1"}]
        )
        monkeypatch.setattr(
            element_context.context_lens_mod,
            "build",
            lambda ref: {
                "focus": {
                    "label": "E=mc^2",
                    "intrinsic_summary": "E=mc^2",
                    "contextual_role": "synth_claim_0001を定量化する",
                    "contextual_role_status": "source_backed",
                    "provenance": ["equation_semantics:eq_1"],
                    "generic": None,
                },
                "upper": [
                    {
                        "element_type": "theory_claim", "element_id": None, "document_id": "doc-1",
                        "label": "synth_claim_0001", "relation": "quantifies",
                        "relation_label": "を定量化する", "relation_status": "source_backed",
                        "evidence_refs": [], "navigable": False,
                    },
                ],
                "lower": [
                    {
                        "element_type": "figure", "element_id": figure_uuid,
                        "document_id": "doc-1", "label": figure_uuid,
                        "relation": "evidenced_by_figure", "relation_label": "を図で裏付ける",
                        "relation_status": "source_backed", "evidence_refs": [], "navigable": True,
                    },
                    {
                        "element_type": "evidence", "element_id": None, "document_id": "doc-1",
                        "label": "ev_0012", "relation": "rests_on_evidence",
                        "relation_label": "を根拠とする", "relation_status": "source_backed",
                        "evidence_refs": ["ev_0012"], "navigable": False,
                    },
                ],
                "notes": [],
            },
        )

        result = get_course_element_context("c1", "equation", "eq_1", {"id": "u1"})

        assert [i["label"] for i in result["upper"]] == ["関連する主張"]
        assert [i["label"] for i in result["lower"]] == ["図", "本文の根拠箇所"]
        # 関係情報は保持する（項目を丸ごと落とさない = 情報を落とさない）。
        assert [i["relation_label"] for i in result["lower"]] == ["を図で裏付ける", "を根拠とする"]
        # figure には学習者向け文脈 API が無いので navigable にしない。
        assert [i["navigable"] for i in result["lower"]] == [False, False]
        assert "contextual_role" not in result["focus"]

        # ITEM.id は契約上残る（教材内ジャンプに使う）。禁じているのは *ラベル* と
        # focus に裸の内部 ID が現れることなので、そこだけを走査する。
        texts = [str(i["label"]) for i in result["upper"] + result["lower"]]
        texts += [str(v) for v in result["focus"].values() if isinstance(v, str)]
        for token in ("synth_claim_0001", "ev_0012", figure_uuid):
            assert all(token not in t for t in texts), token


class TestRouteRegistration:
    def test_route_is_registered_on_learning_router(self):
        from api.routes.learning import router

        paths = {getattr(r, "path", "") for r in router.routes}
        assert (
            "/api/learning/courses/{course_id}/elements/{element_type}/{element_id}/context"
            in paths
        )
