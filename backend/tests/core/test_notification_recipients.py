"""backend/core/notification_recipients.py の単体テスト。

`document_owner_id` / `course_owner_id` / `document_group_member_ids` /
`course_group_member_ids` / `document_legacy_group_visibility_member_ids` という
JOIN プリミティブ単体の正常系・空結果・None 系境界を検証する。

`backend/tests/test_status_guardrails.py` / `test_shared_versioning_guardrails.py` は
このモジュールへの静的ソース検査（関数定義の存在・FastAPI 非 import 等）を既に持つため、
ここでは重複を避け、mock session を使った動的な振る舞い検証と、呼び出し側2箇所
（core/status/notification_rules.py・core/versioning/notifications.py）が実際にこの
モジュールへ委譲していることの回帰チェックに限定する。DB は実接続せず MagicMock で分離する。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.notification_recipients import (
    course_group_member_ids,
    course_owner_id,
    document_group_member_ids,
    document_legacy_group_visibility_member_ids,
    document_owner_id,
)


def _session_returning(*, fetchone=None, fetchall=None):
    session = MagicMock()
    session.execute.return_value.fetchone.return_value = fetchone
    session.execute.return_value.fetchall.return_value = fetchall if fetchall is not None else []
    return session


# ---------------------------------------------------------------------------
# document_owner_id / course_owner_id
# ---------------------------------------------------------------------------


class TestDocumentOwnerId:
    def test_returns_owner_id_when_found(self):
        session = _session_returning(fetchone=("owner-1",))
        assert document_owner_id(session, "doc-1") == "owner-1"

    def test_returns_none_when_document_not_found(self):
        session = _session_returning(fetchone=None)
        assert document_owner_id(session, "missing-doc") is None

    def test_returns_none_when_owner_column_is_null(self):
        session = _session_returning(fetchone=(None,))
        assert document_owner_id(session, "doc-1") is None


class TestCourseOwnerId:
    def test_returns_owner_id_when_found(self):
        session = _session_returning(fetchone=("owner-2",))
        assert course_owner_id(session, "course-1") == "owner-2"

    def test_returns_none_when_course_not_found(self):
        session = _session_returning(fetchone=None)
        assert course_owner_id(session, "missing-course") is None

    def test_returns_none_when_owner_column_is_null(self):
        session = _session_returning(fetchone=(None,))
        assert course_owner_id(session, "course-1") is None


# ---------------------------------------------------------------------------
# document_group_member_ids / course_group_member_ids
# ---------------------------------------------------------------------------


class TestDocumentGroupMemberIds:
    def test_returns_ids_for_matching_rows(self):
        session = _session_returning(fetchall=[("u1",), ("u2",)])
        result = document_group_member_ids(session, "doc-1", ("viewer", "editor"))
        assert result == ["u1", "u2"]

    def test_empty_permissions_short_circuits_without_query(self):
        """境界値: permissions=() は空リストを返し、クエリすら発行しない。"""
        session = MagicMock()
        result = document_group_member_ids(session, "doc-1", ())
        assert result == []
        session.execute.assert_not_called()

    def test_no_matching_rows_returns_empty_list(self):
        session = _session_returning(fetchall=[])
        result = document_group_member_ids(session, "doc-1", ("editor",))
        assert result == []

    def test_single_permission_uses_single_placeholder(self):
        session = _session_returning(fetchall=[])
        document_group_member_ids(session, "doc-1", ("editor",))

        (sql_arg, params_arg), _ = session.execute.call_args
        assert "IN (:p0)" in str(sql_arg)
        assert params_arg == {"did": "doc-1", "p0": "editor"}

    def test_multiple_permissions_build_matching_placeholders_and_params(self):
        session = _session_returning(fetchall=[])
        document_group_member_ids(session, "doc-1", ("viewer", "editor"))

        (sql_arg, params_arg), _ = session.execute.call_args
        assert "IN (:p0, :p1)" in str(sql_arg)
        assert params_arg == {"did": "doc-1", "p0": "viewer", "p1": "editor"}


class TestCourseGroupMemberIds:
    def test_returns_ids_for_matching_rows(self):
        session = _session_returning(fetchall=[("u3",)])
        result = course_group_member_ids(session, "course-1", ("editor",))
        assert result == ["u3"]

    def test_empty_permissions_short_circuits_without_query(self):
        session = MagicMock()
        result = course_group_member_ids(session, "course-1", ())
        assert result == []
        session.execute.assert_not_called()

    def test_no_matching_rows_returns_empty_list(self):
        session = _session_returning(fetchall=[])
        result = course_group_member_ids(session, "course-1", ("viewer",))
        assert result == []

    def test_placeholders_and_params_match_permission_count(self):
        session = _session_returning(fetchall=[])
        course_group_member_ids(session, "course-1", ("viewer", "editor"))

        (sql_arg, params_arg), _ = session.execute.call_args
        assert "IN (:p0, :p1)" in str(sql_arg)
        assert params_arg == {"cid": "course-1", "p0": "viewer", "p1": "editor"}


# ---------------------------------------------------------------------------
# document_legacy_group_visibility_member_ids
# ---------------------------------------------------------------------------


class TestDocumentLegacyGroupVisibilityMemberIds:
    def test_returns_ids_for_matching_rows(self):
        session = _session_returning(fetchall=[("u4",), ("u5",)])
        result = document_legacy_group_visibility_member_ids(session, "doc-1")
        assert result == ["u4", "u5"]

    def test_no_matching_rows_returns_empty_list(self):
        session = _session_returning(fetchall=[])
        result = document_legacy_group_visibility_member_ids(session, "doc-1")
        assert result == []

    def test_query_filters_visibility_group_and_non_null_group_id(self):
        session = _session_returning(fetchall=[])
        document_legacy_group_visibility_member_ids(session, "doc-1")

        (sql_arg, params_arg), _ = session.execute.call_args
        assert "d.visibility = 'group'" in str(sql_arg)
        assert "d.group_id IS NOT NULL" in str(sql_arg)
        assert params_arg == {"did": "doc-1"}


# ---------------------------------------------------------------------------
# 呼び出し側の委譲の回帰チェック（動的 mock。静的ソース検査とは重複しない）
# ---------------------------------------------------------------------------


class TestNotificationRulesDelegation:
    """core/status/notification_rules.py が notification_recipients に委譲していること。"""

    def test_material_recipients_combines_owner_and_editor_only(self):
        from core.status import notification_rules

        with patch.object(notification_rules, "_recipients") as mock_recipients:
            mock_recipients.document_owner_id.return_value = "owner-1"
            mock_recipients.document_group_member_ids.return_value = ["editor-1"]

            result = notification_rules._material_recipients(MagicMock(), "doc-1")

        assert set(result) == {"owner-1", "editor-1"}
        args, _ = mock_recipients.document_group_member_ids.call_args
        assert args[-1] == ("editor",)  # S5: viewer には出さない

    def test_material_recipients_dedupes_owner_equal_to_editor(self):
        from core.status import notification_rules

        with patch.object(notification_rules, "_recipients") as mock_recipients:
            mock_recipients.document_owner_id.return_value = "same-user"
            mock_recipients.document_group_member_ids.return_value = ["same-user"]

            result = notification_rules._material_recipients(MagicMock(), "doc-1")

        assert result == ["same-user"]

    def test_material_recipients_handles_no_owner(self):
        from core.status import notification_rules

        with patch.object(notification_rules, "_recipients") as mock_recipients:
            mock_recipients.document_owner_id.return_value = None
            mock_recipients.document_group_member_ids.return_value = []

            result = notification_rules._material_recipients(MagicMock(), "doc-1")

        assert result == []

    def test_course_recipients_combines_owner_and_editor_only(self):
        from core.status import notification_rules

        with patch.object(notification_rules, "_recipients") as mock_recipients:
            mock_recipients.course_owner_id.return_value = "owner-2"
            mock_recipients.course_group_member_ids.return_value = []

            result = notification_rules._course_recipients(MagicMock(), "course-1")

        assert result == ["owner-2"]
        args, _ = mock_recipients.course_group_member_ids.call_args
        assert args[-1] == ("editor",)


class TestVersioningNotificationsDelegation:
    """core/versioning/notifications.py が notification_recipients に委譲していること。"""

    def test_document_object_combines_dgp_and_legacy_group_visibility(self):
        from core.versioning import notifications as versioning_notifications

        with patch.object(versioning_notifications, "_recipients") as mock_recipients:
            mock_recipients.document_group_member_ids.return_value = ["u1"]
            mock_recipients.document_legacy_group_visibility_member_ids.return_value = ["u2"]

            result = versioning_notifications._recipient_ids(MagicMock(), "document", "doc-1")

        assert set(result) == {"u1", "u2"}
        args, _ = mock_recipients.document_group_member_ids.call_args
        assert args[-1] == ("viewer", "editor")  # V層は viewer も対象

    def test_course_object_uses_viewer_and_editor_only(self):
        from core.versioning import notifications as versioning_notifications

        with patch.object(versioning_notifications, "_recipients") as mock_recipients:
            mock_recipients.course_group_member_ids.return_value = ["u3"]

            result = versioning_notifications._recipient_ids(MagicMock(), "course", "course-1")

        assert result == ["u3"]
        mock_recipients.document_group_member_ids.assert_not_called()
        args, _ = mock_recipients.course_group_member_ids.call_args
        assert args[-1] == ("viewer", "editor")

    def test_document_object_dedupes_overlapping_member_ids(self):
        from core.versioning import notifications as versioning_notifications

        with patch.object(versioning_notifications, "_recipients") as mock_recipients:
            mock_recipients.document_group_member_ids.return_value = ["u1", "u2"]
            mock_recipients.document_legacy_group_visibility_member_ids.return_value = ["u2"]

            result = versioning_notifications._recipient_ids(MagicMock(), "document", "doc-1")

        assert set(result) == {"u1", "u2"}
