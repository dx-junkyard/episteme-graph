"""開示範囲（visibility）変更の監査記帳 — 原則14（監査可能性）。

公開は取り消しの効かない操作（一度出た資料は戻らない）なのに、教材・コースの
``PUT .../visibility`` はログ出力だけで ``theory_review_events`` に何も残していなかった
（共有付与 ``document_share`` や account_lifecycle は記帳している）。ここでは

  1. 記帳が起きること（entity_type / entity_id / 旧・新 visibility / 実行者）
  2. 記帳が「余計なものを載せない」こと（資料本文・受講者情報を metadata に入れない）
  3. 監査語彙がカタログ（``core/schema.py::AUDIT_ENTITY_TYPES``）に登録されていること

を固定する。DB・MinIO へは接続しない（route 関数を直接呼び、モジュール属性を
monkeypatch する ``test_object_scope_authorization.py`` と同じ流儀）。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from api.routes import admin as admin_module  # noqa: E402
from core.schema import AUDIT_ENTITY_TYPES, AUDIT_ENTITY_VISIBILITY  # noqa: E402

USER = "11111111-1111-1111-1111-111111111111"
MATERIAL_ID = "arXiv-2407.01221v2"
COURSE_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
GROUP_ID = "99999999-9999-9999-9999-999999999999"


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    """1本目（旧 visibility の SELECT）と2本目（UPDATE ... RETURNING）を返し分ける。"""

    def __init__(self, previous="private", updated=True):
        self._results = [
            _Rows([(previous,)] if previous is not None else []),
            _Rows([("row-1",)] if updated else []),
        ]
        self.statements: list[str] = []

    def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        return self._results.pop(0) if self._results else _Rows([])

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _Body:
    def __init__(self, visibility="public", group_id=None):
        self.visibility = visibility
        self.group_id = group_id


def _user(role: str = "TEACHER") -> dict:
    return {"id": USER, "username": "u", "email": "u@example.com", "role": role}


@pytest.fixture
def recorded(monkeypatch):
    events: list[tuple] = []
    monkeypatch.setattr(
        admin_module,
        "record_review_event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )
    return events


class TestAuditVocabulary:
    def test_visibility_entity_is_registered_in_the_catalog(self):
        assert AUDIT_ENTITY_VISIBILITY == "visibility"
        assert AUDIT_ENTITY_VISIBILITY in AUDIT_ENTITY_TYPES


class TestMaterialVisibilityIsAudited:
    def test_publish_records_old_and_new_visibility(self, monkeypatch, recorded):
        monkeypatch.setattr(admin_module, "_pg_session", lambda: _FakeSession("private"))

        admin_module.update_material_visibility(
            MATERIAL_ID, body=_Body("public"), current_user=_user(),
        )

        assert len(recorded) == 1
        args, _kwargs = recorded[0]
        entity_type, entity_id, old_status, new_status, actor, metadata = args
        assert entity_type == AUDIT_ENTITY_VISIBILITY
        assert entity_id == MATERIAL_ID
        assert (old_status, new_status) == ("private", "public")
        assert actor == USER
        assert metadata["object_type"] == "document"
        assert metadata["action"] == "material_visibility"

    def test_group_scope_records_the_group(self, monkeypatch, recorded):
        monkeypatch.setattr(admin_module, "_pg_session", lambda: _FakeSession("public"))
        monkeypatch.setattr(admin_module, "user_can_access_group", lambda uid, gid: True)

        admin_module.update_material_visibility(
            MATERIAL_ID, body=_Body("group", GROUP_ID), current_user=_user(),
        )

        _args, _kwargs = recorded[0][0], recorded[0][1]
        metadata = recorded[0][0][5]
        assert metadata["group_id"] == GROUP_ID
        assert recorded[0][0][2:4] == ("public", "group")

    def test_nothing_is_recorded_when_the_update_matches_no_row(self, monkeypatch, recorded):
        """404（他人の教材・不在）では記帳しない（起きなかった変更を残さない）。"""
        monkeypatch.setattr(
            admin_module, "_pg_session", lambda: _FakeSession(None, updated=False),
        )

        with pytest.raises(admin_module.HTTPException) as exc:
            admin_module.update_material_visibility(
                MATERIAL_ID, body=_Body("public"), current_user=_user(),
            )
        assert exc.value.status_code == 404
        assert recorded == []

    def test_metadata_carries_no_content_or_learner_information(self, monkeypatch, recorded):
        monkeypatch.setattr(admin_module, "_pg_session", lambda: _FakeSession("private"))

        admin_module.update_material_visibility(
            MATERIAL_ID, body=_Body("public"), current_user=_user(),
        )

        metadata = recorded[0][0][5]
        assert set(metadata) == {"action", "object_type", "group_id"}


class TestCourseVisibilityIsAudited:
    def test_publish_records_old_and_new_visibility(self, monkeypatch, recorded):
        monkeypatch.setattr(admin_module, "_pg_session", lambda: _FakeSession("private"))

        admin_module.update_course_visibility(
            COURSE_ID, body=_Body("public"), current_user=_user(),
        )

        entity_type, entity_id, old_status, new_status, actor, metadata = recorded[0][0]
        assert entity_type == AUDIT_ENTITY_VISIBILITY
        assert entity_id == COURSE_ID
        assert (old_status, new_status) == ("private", "public")
        assert actor == USER
        assert metadata["object_type"] == "course"
        assert metadata["action"] == "course_visibility"

    def test_unpublish_is_recorded_too(self, monkeypatch, recorded):
        """公開の解除も状態遷移なので記帳する（片方向だけ残さない）。"""
        monkeypatch.setattr(admin_module, "_pg_session", lambda: _FakeSession("public"))

        admin_module.update_course_visibility(
            COURSE_ID, body=_Body("private"), current_user=_user(),
        )

        assert recorded[0][0][2:4] == ("public", "private")

    def test_nothing_is_recorded_when_the_update_matches_no_row(self, monkeypatch, recorded):
        monkeypatch.setattr(
            admin_module, "_pg_session", lambda: _FakeSession(None, updated=False),
        )

        with pytest.raises(admin_module.HTTPException) as exc:
            admin_module.update_course_visibility(
                COURSE_ID, body=_Body("public"), current_user=_user(),
            )
        assert exc.value.status_code == 404
        assert recorded == []

    def test_release_wizard_provenance_is_not_fabricated(self):
        """ウィザード経由かどうかはサーバから判別できない → 申告しない（偽装しない）。

        2026-09-10（是正 A-04 / F-18）で公開に `decision_context` を足したが、
        「どの画面から来たか」の申告は増やしていない（`client_reported` を使わない）。
        """
        from pathlib import Path

        from tests.guardrail_helpers import extract_function_source

        src = extract_function_source(
            (Path(__file__).resolve().parents[1] / "api" / "routes" / "admin.py").read_text(
                encoding="utf-8"
            ),
            "update_course_visibility",
        )
        assert "client_reported" not in src
        assert "release_review" not in src

    def test_publish_carries_the_decision_context(self, monkeypatch, recorded):
        """DC1: 公開は「取り消しの効かない確定」なので確定文脈を伴う。"""
        from core import decision_context as dc

        monkeypatch.setattr(admin_module, "_pg_session", lambda: _FakeSession("private"))

        admin_module.update_course_visibility(
            COURSE_ID, body=_Body("public"), current_user=_user(),
        )

        metadata = recorded[0][0][5]
        ctx = metadata[dc.DECISION_CONTEXT_KEY]
        assert ctx["basis"] == dc.BASIS_COURSE_VISIBILITY_PUBLISH
        # 確定の対象はコース1件（提示と適用は同じ — DC2 の差の検出を殺さない）。
        assert ctx["presented"]["ids"] == [COURSE_ID]
        assert ctx["applied"]["ids"] == [COURSE_ID]
        assert ctx["presented_matches_applied"] is True
        assert ctx["alternatives_available"] == ["skip_step"]
        assert ctx["decline_possible"] is True
        assert ctx["reopen"]["path"].endswith("/visibility")
        assert ctx["reopen"]["statuses"] == ["group", "private"]
        assert ctx["reopen"]["actor"] == "teacher"
        assert ctx["evidence_shown"] is None
        assert ctx["client_reported"] is None
        # 既存キーは不変（additive）。
        assert metadata["action"] == "course_visibility"
        assert metadata["object_type"] == "course"

    @pytest.mark.parametrize("visibility", ["group", "private"])
    def test_non_public_transitions_carry_no_decision_context(
        self, monkeypatch, recorded, visibility
    ):
        """公開だけが「一度出たら戻らない」確定。非公開化は従来の記帳のまま。"""
        from core import decision_context as dc

        monkeypatch.setattr(admin_module, "_pg_session", lambda: _FakeSession("public"))
        monkeypatch.setattr(admin_module, "user_can_access_group", lambda uid, gid: True)

        admin_module.update_course_visibility(
            COURSE_ID,
            body=_Body(visibility, GROUP_ID if visibility == "group" else None),
            current_user=_user(),
        )

        metadata = recorded[0][0][5]
        assert dc.DECISION_CONTEXT_KEY not in metadata
        assert set(metadata) == {"action", "object_type", "group_id"}

    def test_response_shape_is_unchanged(self, monkeypatch, recorded):
        """B（記帳のみ・挙動不変）: レスポンスに確定文脈を足していない。"""
        monkeypatch.setattr(admin_module, "_pg_session", lambda: _FakeSession("private"))

        out = admin_module.update_course_visibility(
            COURSE_ID, body=_Body("public"), current_user=_user(),
        )
        assert set(out) == {"course_id", "visibility", "group_id"}
