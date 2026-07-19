"""Phase 4: 図のコース流通（§7.2 記法解決・§7.3 学習者配信）の回帰テスト。

対象:
  - ``backend/api/routes/learning.py::get_topic_material`` の figures 配線
    （``![[figure:id]]`` → ``[[FIGURE_N]]`` 解決 + figures 配列）
  - ``backend/api/routes/learning.py::get_course_figure_image`` — 学習者向け図画像配信の
    3条件 fail-closed ゲート（受講ゲート / コースソース document 判定 / コース内容からの
    実参照判定）
  - 上記が使う純粋ヘルパ（``_course_references_figure`` / ``_topic_linked_figure_ids``）

DB/MinIO は一切呼び出さない（境界（``get_accessible_course_data`` /
``_load_figure_row_by_id`` / ``_course_document_ids`` / ``get_storage_client``）のみ
モックし、ゲート判定ロジック自体は実データで検証する。test_lecture_slides.py の
``TestSequenceApiSlides``（``@patch("api.routes.lecture.X")``）と同型の手法。
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# api/ ディレクトリを sys.path に追加して schemas 等をインポート可能にする
# （backend/api/routes/*.py は内部で `from schemas import ...` のような裸 import を使う）。
_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


_FIG_UUID = "550e8400-e29b-41d4-a716-446655440001"
_DOC_UUID = "550e8400-e29b-41d4-a716-446655440002"


def _course_data(topics, sources=None):
    return {"topics": topics, "sources": sources if sources is not None else [{"material_id": "mat-1"}]}


# ---------------------------------------------------------------------------
# get_topic_material: figures 配線（§7.2）
# ---------------------------------------------------------------------------


class TestGetTopicMaterialFigures:
    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_figure_embed_resolved_to_placeholder_with_descriptor(self, mock_course, mock_figs):
        from api.routes.learning import get_topic_material

        mock_course.return_value = _course_data([{
            "id": "topic-1",
            "student_material": {"source_text": f"導入本文 ![[figure:{_FIG_UUID}]] まとめ"},
        }])
        image_url = f"/api/learning/courses/c1/figures/{_FIG_UUID}/image"
        mock_figs.return_value = {_FIG_UUID: {"caption": "装置の写真", "image_url": image_url}}

        resp = get_topic_material("c1", "topic-1", {"id": "u1"})

        chunk = resp.chunks[0]
        assert "[[FIGURE_1]]" in chunk.text
        assert f"![[figure:{_FIG_UUID}]]" not in chunk.text
        assert len(chunk.figures) == 1
        fig = chunk.figures[0]
        assert fig["figure_id"] == _FIG_UUID
        assert fig["caption"] == "装置の写真"
        assert fig["explanation"] is None
        assert fig["image_url"] == image_url
        # _load_course_figures_by_id はコース単位で呼ばれる（course_id を渡す）
        mock_figs.assert_called_once_with("c1", mock_course.return_value)

    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_unsupplied_figure_id_left_unresolved(self, mock_course, mock_figs):
        """figures_by_id に無い figure_id は原文のまま残す（情報を落とさない）。"""
        from api.routes.learning import get_topic_material

        mock_course.return_value = _course_data([{
            "id": "topic-1",
            "student_material": {"source_text": f"導入 ![[figure:{_FIG_UUID}]] 続き"},
        }])
        mock_figs.return_value = {}

        resp = get_topic_material("c1", "topic-1", {"id": "u1"})

        chunk = resp.chunks[0]
        assert f"![[figure:{_FIG_UUID}]]" in chunk.text
        assert chunk.figures == []

    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_topic_without_figure_embed_has_empty_figures(self, mock_course, mock_figs):
        from api.routes.learning import get_topic_material

        mock_course.return_value = _course_data([{
            "id": "topic-1",
            "student_material": {"source_text": "図の埋め込みがない普通の本文"},
        }])
        mock_figs.return_value = {}

        resp = get_topic_material("c1", "topic-1", {"id": "u1"})

        assert resp.chunks[0].figures == []

    @patch("core.element_explanations.approved_for_elements")
    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_approved_contextual_explanation_is_filled_into_descriptor(
        self, mock_course, mock_figs, mock_approved,
    ):
        """Phase 2 §5.3: 承認済み(approved) contextual element_explanations があれば
        図デスクリプタの ``explanation`` に充填される（``_attach_figure_explanations`` 経由）。"""
        from api.routes.learning import get_topic_material
        from core import element_explanations

        mock_course.return_value = _course_data([{
            "id": "topic-1",
            "student_material": {"source_text": f"導入本文 ![[figure:{_FIG_UUID}]] まとめ"},
        }])
        mock_figs.return_value = {_FIG_UUID: {
            "caption": "装置の写真",
            "image_url": f"/api/learning/courses/c1/figures/{_FIG_UUID}/image",
            "document_id": _DOC_UUID,
        }}
        mock_approved.return_value = {
            (element_explanations.ELEMENT_TYPE_FIGURE, _FIG_UUID): [{
                "kind": element_explanations.KIND_CONTEXTUAL,
                "body": "この図は§3の測定配置を示し、主張Xを支える。",
                "status": element_explanations.STATUS_APPROVED,
            }],
        }

        resp = get_topic_material("c1", "topic-1", {"id": "u1"})

        fig = resp.chunks[0].figures[0]
        assert fig["explanation"] == "この図は§3の測定配置を示し、主張Xを支える。"
        mock_approved.assert_called_once()
        # approved_for_elements は document 単位で呼ぶ (document_id, [(figure, id)])
        called_document_id, called_refs = mock_approved.call_args.args[1:3]
        assert called_document_id == _DOC_UUID
        assert called_refs == [(element_explanations.ELEMENT_TYPE_FIGURE, _FIG_UUID)]

    @patch("core.element_explanations.approved_for_elements")
    @patch("api.routes.learning._load_course_figures_by_id")
    @patch("api.routes.learning.get_course_data")
    def test_no_approved_explanation_leaves_descriptor_explanation_none(
        self, mock_course, mock_figs, mock_approved,
    ):
        """approved_for_elements が空({}) を返す（未承認・未生成 document の通常状態）場合は
        explanation は None のまま（候補/未承認の本文が漏れることはない）。"""
        from api.routes.learning import get_topic_material

        mock_course.return_value = _course_data([{
            "id": "topic-1",
            "student_material": {"source_text": f"導入本文 ![[figure:{_FIG_UUID}]] まとめ"},
        }])
        mock_figs.return_value = {_FIG_UUID: {
            "caption": "装置の写真",
            "image_url": f"/api/learning/courses/c1/figures/{_FIG_UUID}/image",
            "document_id": _DOC_UUID,
        }}
        mock_approved.return_value = {}

        resp = get_topic_material("c1", "topic-1", {"id": "u1"})

        assert resp.chunks[0].figures[0]["explanation"] is None


class TestAttachFigureExplanations:
    """``routes.lecture._attach_figure_explanations`` の単体テスト（Phase 2 §5.3）。

    ``routes.learning.get_topic_material`` / ``routes.lecture._build_topic_draft_segment``
    の双方がこのヘルパーを共有するため、ここでは境界（``element_explanations.approved_for_elements``
    と ``_pg_session``）だけをモックし、フィルタ・グルーピングのロジック自体を検証する。
    """

    @patch("core.element_explanations.approved_for_elements")
    @patch("api.routes.lecture._pg_session")
    def test_contextual_kind_is_used(self, mock_session, mock_approved):
        from api.routes.lecture import _attach_figure_explanations
        from core import element_explanations

        mock_session.return_value = MagicMock()
        mock_approved.return_value = {
            (element_explanations.ELEMENT_TYPE_FIGURE, _FIG_UUID): [{
                "kind": element_explanations.KIND_CONTEXTUAL,
                "body": "文脈説明本文",
                "status": element_explanations.STATUS_APPROVED,
            }],
        }
        figures = [{"figure_id": _FIG_UUID, "caption": "c", "explanation": None, "image_url": None}]
        figures_by_id = {_FIG_UUID: {"document_id": _DOC_UUID}}

        _attach_figure_explanations(figures, figures_by_id)

        assert figures[0]["explanation"] == "文脈説明本文"

    @patch("core.element_explanations.approved_for_elements")
    @patch("api.routes.lecture._pg_session")
    def test_generic_only_kind_is_not_used_for_descriptor(self, mock_session, mock_approved):
        """descriptor 充填は contextual 専用（generic は別用途 — E1 の二層分離を維持）。"""
        from api.routes.lecture import _attach_figure_explanations
        from core import element_explanations

        mock_session.return_value = MagicMock()
        mock_approved.return_value = {
            (element_explanations.ELEMENT_TYPE_FIGURE, _FIG_UUID): [{
                "kind": element_explanations.KIND_GENERIC,
                "body": "一般的な説明本文",
                "status": element_explanations.STATUS_APPROVED,
            }],
        }
        figures = [{"figure_id": _FIG_UUID, "caption": "c", "explanation": None, "image_url": None}]
        figures_by_id = {_FIG_UUID: {"document_id": _DOC_UUID}}

        _attach_figure_explanations(figures, figures_by_id)

        assert figures[0]["explanation"] is None

    @patch("core.element_explanations.approved_for_elements")
    @patch("api.routes.lecture._pg_session")
    def test_missing_document_id_skips_lookup_without_error(self, mock_session, mock_approved):
        """figures_by_id に document_id が無い（未解決）図は DB を読まずスキップする
        （情報を落とさない = 例外にせず explanation は None のまま）。"""
        from api.routes.lecture import _attach_figure_explanations

        figures = [{"figure_id": _FIG_UUID, "caption": "c", "explanation": None, "image_url": None}]
        figures_by_id = {_FIG_UUID: {"caption": "c"}}  # document_id 無し

        _attach_figure_explanations(figures, figures_by_id)

        assert figures[0]["explanation"] is None
        mock_approved.assert_not_called()
        mock_session.assert_not_called()

    def test_empty_figures_list_is_a_noop(self):
        from api.routes.lecture import _attach_figure_explanations

        figures: list = []
        _attach_figure_explanations(figures, {})
        assert figures == []


# ---------------------------------------------------------------------------
# 純粋ヘルパ: _course_references_figure / _topic_linked_figure_ids
# ---------------------------------------------------------------------------


class TestTopicLinkedFigureIds:
    def test_reads_from_dict_topic(self):
        from api.routes.learning import _topic_linked_figure_ids

        assert _topic_linked_figure_ids({"linked_figure_ids": [_FIG_UUID, ""]}) == [_FIG_UUID]

    def test_missing_key_returns_empty_list(self):
        from api.routes.learning import _topic_linked_figure_ids

        assert _topic_linked_figure_ids({}) == []

    def test_falls_back_to_getattr_for_non_dict_topic(self):
        """並行実装中の CourseTopic（extra="allow"）インスタンスにも防御的に対応する。"""
        from api.routes.learning import _topic_linked_figure_ids

        class _FakeTopic:
            linked_figure_ids = [_FIG_UUID]

        assert _topic_linked_figure_ids(_FakeTopic()) == [_FIG_UUID]

    def test_object_without_attribute_returns_empty_list(self):
        from api.routes.learning import _topic_linked_figure_ids

        class _FakeTopic:
            pass

        assert _topic_linked_figure_ids(_FakeTopic()) == []


class TestCourseReferencesFigure:
    def test_true_when_embed_in_student_material(self):
        from api.routes.learning import _course_references_figure

        course_data = _course_data([{
            "id": "t1", "student_material": {"source_text": f"本文 ![[figure:{_FIG_UUID}]]"},
        }])
        assert _course_references_figure(course_data, _FIG_UUID) is True

    def test_true_when_linked_figure_ids_contains_id(self):
        from api.routes.learning import _course_references_figure

        course_data = _course_data([{
            "id": "t1", "student_material": {"source_text": "無関係"},
            "linked_figure_ids": [_FIG_UUID],
        }])
        assert _course_references_figure(course_data, _FIG_UUID) is True

    def test_false_when_not_referenced_anywhere(self):
        from api.routes.learning import _course_references_figure

        course_data = _course_data([{"id": "t1", "student_material": {"source_text": "無関係な本文"}}])
        assert _course_references_figure(course_data, _FIG_UUID) is False

    def test_false_for_course_with_no_topics(self):
        from api.routes.learning import _course_references_figure

        assert _course_references_figure(_course_data([]), _FIG_UUID) is False


# ---------------------------------------------------------------------------
# get_course_figure_image: 3条件 fail-closed ゲート（§7.3）
# ---------------------------------------------------------------------------


class TestGetCourseFigureImageGating:
    @patch("api.routes.learning.get_accessible_course_data", return_value=None)
    def test_inaccessible_course_returns_404(self, _mock_course):
        from fastapi import HTTPException

        from api.routes.learning import get_course_figure_image

        with pytest.raises(HTTPException) as exc:
            get_course_figure_image("c1", _FIG_UUID, {"id": "u1"})
        assert exc.value.status_code == 404

    @patch("api.routes.learning.get_accessible_course_data")
    def test_non_uuid_figure_id_returns_404(self, mock_course):
        from fastapi import HTTPException

        from api.routes.learning import get_course_figure_image

        mock_course.return_value = _course_data([])
        with pytest.raises(HTTPException) as exc:
            get_course_figure_image("c1", "not-a-uuid", {"id": "u1"})
        assert exc.value.status_code == 404

    @patch("api.routes.learning._load_figure_row_by_id", return_value=None)
    @patch("api.routes.learning.get_accessible_course_data")
    def test_missing_figure_row_returns_404(self, mock_course, _mock_row):
        from fastapi import HTTPException

        from api.routes.learning import get_course_figure_image

        mock_course.return_value = _course_data([])
        with pytest.raises(HTTPException) as exc:
            get_course_figure_image("c1", _FIG_UUID, {"id": "u1"})
        assert exc.value.status_code == 404

    @patch("api.routes.learning._load_figure_row_by_id", return_value={
        "id": _FIG_UUID, "document_id": _DOC_UUID, "minio_key": None,
    })
    @patch("api.routes.learning.get_accessible_course_data")
    def test_figure_row_without_minio_key_returns_404(self, mock_course, _mock_row):
        from fastapi import HTTPException

        from api.routes.learning import get_course_figure_image

        mock_course.return_value = _course_data([])
        with pytest.raises(HTTPException) as exc:
            get_course_figure_image("c1", _FIG_UUID, {"id": "u1"})
        assert exc.value.status_code == 404

    @patch("api.routes.learning._course_document_ids", return_value=["some-other-document-id"])
    @patch("api.routes.learning._load_figure_row_by_id")
    @patch("api.routes.learning.get_accessible_course_data")
    def test_figure_document_outside_course_sources_returns_404(
        self, mock_course, mock_row, _mock_docids,
    ):
        """条件2失敗: 図の document がコースソースに含まれない。"""
        from fastapi import HTTPException

        from api.routes.learning import get_course_figure_image

        mock_course.return_value = _course_data([{
            "id": "t1", "student_material": {"source_text": f"![[figure:{_FIG_UUID}]]"},
        }])
        mock_row.return_value = {"id": _FIG_UUID, "document_id": _DOC_UUID, "minio_key": "k1"}

        with pytest.raises(HTTPException) as exc:
            get_course_figure_image("c1", _FIG_UUID, {"id": "u1"})
        assert exc.value.status_code == 404

    @patch("api.routes.learning._course_document_ids", return_value=[_DOC_UUID])
    @patch("api.routes.learning._load_figure_row_by_id")
    @patch("api.routes.learning.get_accessible_course_data")
    def test_figure_not_referenced_from_course_content_returns_404(
        self, mock_course, mock_row, _mock_docids,
    ):
        """条件3失敗: document はコースソースに含まれるが、どのトピックからも参照されない。"""
        from fastapi import HTTPException

        from api.routes.learning import get_course_figure_image

        mock_course.return_value = _course_data([
            {"id": "t1", "student_material": {"source_text": "無関係な本文"}},
        ])
        mock_row.return_value = {"id": _FIG_UUID, "document_id": _DOC_UUID, "minio_key": "k1"}

        with pytest.raises(HTTPException) as exc:
            get_course_figure_image("c1", _FIG_UUID, {"id": "u1"})
        assert exc.value.status_code == 404

    @patch("api.routes.learning.get_storage_client")
    @patch("api.routes.learning._course_document_ids", return_value=[_DOC_UUID])
    @patch("api.routes.learning._load_figure_row_by_id")
    @patch("api.routes.learning.get_accessible_course_data")
    def test_all_conditions_satisfied_returns_image_bytes(
        self, mock_course, mock_row, _mock_docids, mock_storage,
    ):
        from api.routes.learning import get_course_figure_image

        mock_course.return_value = _course_data([{
            "id": "t1",
            "student_material": {"source_text": f"本文 ![[figure:{_FIG_UUID}]] 続き"},
        }])
        mock_row.return_value = {"id": _FIG_UUID, "document_id": _DOC_UUID, "minio_key": "figures/k1.png"}
        mock_client = MagicMock()
        mock_client.get_object.return_value = b"PNGDATA"
        mock_storage.return_value = mock_client

        resp = get_course_figure_image("c1", _FIG_UUID, {"id": "u1"})

        assert resp.body == b"PNGDATA"
        assert resp.media_type == "image/png"
        mock_client.get_object.assert_called_once_with("figure-images", "figures/k1.png")

    @patch("api.routes.learning.get_storage_client")
    @patch("api.routes.learning._course_document_ids", return_value=[_DOC_UUID])
    @patch("api.routes.learning._load_figure_row_by_id")
    @patch("api.routes.learning.get_accessible_course_data")
    def test_referenced_via_linked_figure_ids_also_satisfies_condition3(
        self, mock_course, mock_row, _mock_docids, mock_storage,
    ):
        from api.routes.learning import get_course_figure_image

        mock_course.return_value = _course_data([{
            "id": "t1",
            "student_material": {"source_text": "埋め込みは無いが linked_figure_ids で紐づく"},
            "linked_figure_ids": [_FIG_UUID],
        }])
        mock_row.return_value = {"id": _FIG_UUID, "document_id": _DOC_UUID, "minio_key": "k1"}
        mock_client = MagicMock()
        mock_client.get_object.return_value = b"BYTES"
        mock_storage.return_value = mock_client

        resp = get_course_figure_image("c1", _FIG_UUID, {"id": "u1"})
        assert resp.body == b"BYTES"

    @patch("api.routes.learning.get_storage_client")
    @patch("api.routes.learning._course_document_ids", return_value=[_DOC_UUID])
    @patch("api.routes.learning._load_figure_row_by_id")
    @patch("api.routes.learning.get_accessible_course_data")
    def test_minio_fetch_failure_returns_404(
        self, mock_course, mock_row, _mock_docids, mock_storage,
    ):
        from fastapi import HTTPException

        from api.routes.learning import get_course_figure_image

        mock_course.return_value = _course_data([{
            "id": "t1", "student_material": {"source_text": f"![[figure:{_FIG_UUID}]]"},
        }])
        mock_row.return_value = {"id": _FIG_UUID, "document_id": _DOC_UUID, "minio_key": "k1"}
        mock_client = MagicMock()
        mock_client.get_object.side_effect = Exception("boom")
        mock_storage.return_value = mock_client

        with pytest.raises(HTTPException) as exc:
            get_course_figure_image("c1", _FIG_UUID, {"id": "u1"})
        assert exc.value.status_code == 404
