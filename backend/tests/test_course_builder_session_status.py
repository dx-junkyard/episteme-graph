"""Issue #310: コース構築セッションのステータス管理・命名改善のテスト。

_generate_session_display_name() の命名ロジック、CourseBuilderSession* スキーマの
新フィールド、およびAPIエンドポイントの新フィールド対応を検証する。
外部 API・DB接続は一切行わない。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_backend_dir = str(Path(__file__).resolve().parents[1])
_api_dir = str(Path(__file__).resolve().parents[1] / "api")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _api_dir not in sys.path:
    sys.path.insert(0, _api_dir)

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

class TestCourseBuilderSessionSchemas:
    def test_session_out_new_fields_defaults(self):
        from schemas import CourseBuilderSessionOut
        out = CourseBuilderSessionOut(
            session_id="abc", title="タイトル",
            created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
        )
        assert out.display_name is None
        assert out.source_file_name is None
        assert out.status == "draft"
        assert out.published_course_id is None

    def test_session_out_all_fields(self):
        from schemas import CourseBuilderSessionOut
        out = CourseBuilderSessionOut(
            session_id="abc",
            title="テスト",
            display_name="paper_20260101_1200_テスト",
            source_file_name="paper",
            status="published",
            published_course_id="course-123",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        assert out.status == "published"
        assert out.published_course_id == "course-123"
        assert out.display_name == "paper_20260101_1200_テスト"

    def test_session_create_defaults(self):
        from schemas import CourseBuilderSessionCreate
        body = CourseBuilderSessionCreate()
        assert body.title == ""
        assert body.source_file_name is None
        assert body.display_name is None

    def test_session_create_with_fields(self):
        from schemas import CourseBuilderSessionCreate
        body = CourseBuilderSessionCreate(
            title="DHOST理論コース",
            source_file_name="2407.01221v2",
        )
        assert body.title == "DHOST理論コース"
        assert body.source_file_name == "2407.01221v2"

    def test_session_update_all_optional(self):
        from schemas import CourseBuilderSessionUpdate
        body = CourseBuilderSessionUpdate()
        assert body.title is None
        assert body.history is None
        assert body.course_draft is None
        assert body.source_file_name is None
        assert body.display_name is None
        assert body.status is None
        assert body.published_course_id is None

    def test_session_update_partial_status(self):
        from schemas import CourseBuilderSessionUpdate
        body = CourseBuilderSessionUpdate(status="published", published_course_id="course-456")
        assert body.status == "published"
        assert body.published_course_id == "course-456"
        assert body.title is None


# ---------------------------------------------------------------------------
# _generate_session_display_name() tests
# ---------------------------------------------------------------------------

class TestGenerateSessionDisplayName:
    def _get_fn(self):
        import routes.admin as admin_mod
        return admin_mod._generate_session_display_name

    def test_with_source_and_title(self):
        fn = self._get_fn()
        name = fn("2407.01221v2", "DHOST理論コース")
        assert name.startswith("2407.01221v2_")
        assert "DHOST理論コース" in name

    def test_without_source(self):
        fn = self._get_fn()
        name = fn(None, "局所バイアスモデル入門")
        assert "局所バイアスモデル入門" in name
        assert not name.startswith("_")

    def test_empty_title_uses_default(self):
        fn = self._get_fn()
        name = fn("paper123", "")
        assert "新規コース設計" in name

    def test_none_title_uses_default(self):
        fn = self._get_fn()
        name = fn(None, None)
        assert "新規コース設計" in name

    def test_long_title_is_truncated(self):
        fn = self._get_fn()
        long_title = "あ" * 50
        name = fn("src", long_title)
        # title part (last underscore-segment) should be <= 20 chars
        title_part = name.rsplit("_", 1)[-1]
        assert len(title_part) <= 20

    def test_long_source_is_truncated(self):
        fn = self._get_fn()
        long_src = "x" * 60
        name = fn(long_src, "タイトル")
        src_part = name.split("_")[0]
        assert len(src_part) <= 40

    def test_forbidden_chars_replaced(self):
        fn = self._get_fn()
        name = fn("file/with:slash", 'title<>bad"chars')
        assert "/" not in name
        assert ":" not in name
        assert "<" not in name
        assert ">" not in name
        assert '"' not in name


# ---------------------------------------------------------------------------
# list_cb_sessions — mock row: (id, title, created_at, updated_at,
#                                source_file_name, display_name, status, published_course_id)
# ---------------------------------------------------------------------------

def _list_row(session_id="s1", title="T", status="draft",
              display_name="dn", source_file_name="src", published_course_id=None):
    return (session_id, title, _TS, _TS, source_file_name, display_name, status, published_course_id)


class TestListCbSessions:
    def test_returns_new_fields(self):
        import routes.admin as admin_mod
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [_list_row()]
        with patch("routes.admin._pg_session", return_value=mock_session):
            result = admin_mod.list_cb_sessions(current_user={"id": "user-1"})
        assert len(result) == 1
        assert result[0].status == "draft"
        assert result[0].display_name == "dn"
        assert result[0].source_file_name == "src"
        assert result[0].published_course_id is None

    def test_published_session_returned(self):
        import routes.admin as admin_mod
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            _list_row(status="published", published_course_id="course-999"),
        ]
        with patch("routes.admin._pg_session", return_value=mock_session):
            result = admin_mod.list_cb_sessions(current_user={"id": "user-1"})
        assert result[0].status == "published"
        assert result[0].published_course_id == "course-999"

    def test_empty_list(self):
        import routes.admin as admin_mod
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = []
        with patch("routes.admin._pg_session", return_value=mock_session):
            result = admin_mod.list_cb_sessions(current_user={"id": "user-1"})
        assert result == []


# ---------------------------------------------------------------------------
# get_cb_session — mock row: (id, title, history, course_draft, created_at, updated_at,
#                              source_file_name, display_name, status, published_course_id)
# ---------------------------------------------------------------------------

def _get_row(session_id="s1", title="T", history=None, course_draft=None,
             status="draft", display_name="dn", source_file_name="src",
             published_course_id=None):
    return (session_id, title, history or [], course_draft,
            _TS, _TS, source_file_name, display_name, status, published_course_id)


class TestGetCbSession:
    def test_returns_all_new_fields(self):
        import routes.admin as admin_mod
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = _get_row(
            status="published", published_course_id="course-42",
            source_file_name="arxiv_2407", display_name="arxiv_2407_20260101_1200_テスト",
        )
        with patch("routes.admin._pg_session", return_value=mock_session):
            result = admin_mod.get_cb_session("s1", current_user={"id": "user-1"})
        assert result["status"] == "published"
        assert result["published_course_id"] == "course-42"
        assert result["source_file_name"] == "arxiv_2407"
        assert result["display_name"] == "arxiv_2407_20260101_1200_テスト"

    def test_draft_status_returned(self):
        import routes.admin as admin_mod
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = _get_row(status="draft")
        with patch("routes.admin._pg_session", return_value=mock_session):
            result = admin_mod.get_cb_session("s1", current_user={"id": "user-1"})
        assert result["status"] == "draft"
        assert result["published_course_id"] is None

    def test_404_when_not_found(self):
        import routes.admin as admin_mod
        from fastapi import HTTPException
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        with patch("routes.admin._pg_session", return_value=mock_session):
            with pytest.raises(HTTPException) as exc_info:
                admin_mod.get_cb_session("missing-id", current_user={"id": "user-1"})
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# create_cb_session
# ---------------------------------------------------------------------------

class TestCreateCbSession:
    def _mock_db(self):
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = (_TS, _TS)
        return mock_session

    def test_generates_display_name_automatically(self):
        import routes.admin as admin_mod
        from schemas import CourseBuilderSessionCreate
        body = CourseBuilderSessionCreate(title="新しいコース", source_file_name="2407.01221v2")
        with patch("routes.admin._pg_session", return_value=self._mock_db()):
            result = admin_mod.create_cb_session(body=body, current_user={"id": "user-1"})
        assert result.status == "draft"
        assert result.source_file_name == "2407.01221v2"
        assert result.display_name is not None
        assert "2407.01221v2" in result.display_name
        assert result.published_course_id is None

    def test_uses_provided_display_name(self):
        import routes.admin as admin_mod
        from schemas import CourseBuilderSessionCreate
        body = CourseBuilderSessionCreate(
            title="テスト", source_file_name="paper", display_name="custom_display",
        )
        with patch("routes.admin._pg_session", return_value=self._mock_db()):
            result = admin_mod.create_cb_session(body=body, current_user={"id": "user-1"})
        assert result.display_name == "custom_display"

    def test_empty_title_uses_default_in_display_name(self):
        import routes.admin as admin_mod
        from schemas import CourseBuilderSessionCreate
        body = CourseBuilderSessionCreate(title="", source_file_name=None)
        with patch("routes.admin._pg_session", return_value=self._mock_db()):
            result = admin_mod.create_cb_session(body=body, current_user={"id": "user-1"})
        assert result.title == ""
        assert "新規コース設計" in (result.display_name or "")


# ---------------------------------------------------------------------------
# update_cb_session — existing row: (title, history, course_draft, created_at,
#                                     source_file_name, display_name, status, published_course_id)
# ---------------------------------------------------------------------------

def _update_existing(title="旧タイトル", status="draft"):
    return (title, [], None, _TS, "paper", "paper_old_display", status, None)


class TestUpdateCbSession:
    def _mock_db(self, existing):
        mock_session = MagicMock()
        fetchone = mock_session.execute.return_value.fetchone
        fetchone.side_effect = [existing, (datetime(2026, 1, 2, tzinfo=timezone.utc),)]
        return mock_session

    def test_update_status_to_published(self):
        import routes.admin as admin_mod
        from schemas import CourseBuilderSessionUpdate
        body = CourseBuilderSessionUpdate(status="published", published_course_id="course-123")
        with patch("routes.admin._pg_session", return_value=self._mock_db(_update_existing())):
            result = admin_mod.update_cb_session("s1", body=body, current_user={"id": "user-1"})
        assert result.status == "published"
        assert result.published_course_id == "course-123"

    def test_preserves_existing_fields_when_not_updated(self):
        import routes.admin as admin_mod
        from schemas import CourseBuilderSessionUpdate
        body = CourseBuilderSessionUpdate(title="新タイトル")
        with patch("routes.admin._pg_session", return_value=self._mock_db(_update_existing())):
            result = admin_mod.update_cb_session("s1", body=body, current_user={"id": "user-1"})
        assert result.title == "新タイトル"
        assert result.status == "draft"
        assert result.source_file_name == "paper"
        assert result.display_name == "paper_old_display"

    def test_already_published_preserves_status(self):
        import routes.admin as admin_mod
        from schemas import CourseBuilderSessionUpdate
        body = CourseBuilderSessionUpdate(title="別タイトル")
        existing = _update_existing(status="published")
        existing = list(existing)
        existing[7] = "course-old"  # published_course_id
        existing = tuple(existing)
        with patch("routes.admin._pg_session", return_value=self._mock_db(existing)):
            result = admin_mod.update_cb_session("s1", body=body, current_user={"id": "user-1"})
        assert result.status == "published"
        assert result.published_course_id == "course-old"

    def test_404_when_not_found(self):
        import routes.admin as admin_mod
        from schemas import CourseBuilderSessionUpdate
        from fastapi import HTTPException
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        body = CourseBuilderSessionUpdate(title="X")
        with patch("routes.admin._pg_session", return_value=mock_session):
            with pytest.raises(HTTPException) as exc_info:
                admin_mod.update_cb_session("missing", body=body, current_user={"id": "user-1"})
        assert exc_info.value.status_code == 404
