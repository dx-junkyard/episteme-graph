"""原稿スタジオのトピック保存の監査記帳 — 原則14（監査可能性・是正 F11）。

`PUT /api/admin/courses/{id}/lecture-studio/course-topics/{topic_id}` は**学習者に
配信される**授業用教材・読み上げ原稿を上書きし、副作用として当該トピックの生成済み
音声を消すのに、`theory_review_events` へ何も残していなかった
（[六つのレンズ調査](../../docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md)
§4 第1波 #6 / 是正 F11）。ここでは

  1. 記帳が起きること（entity_type / course_id / 実行者）
  2. **変わったフィールド名だけ**を載せること（本文は監査に入れない）
  3. 音声キャッシュ無効化という副作用も事実として残ること
  4. 権限で弾かれたとき（403 / 404）は記帳しないこと

を固定する。DB へは接続せず、route 関数を直接呼んでモジュール属性を monkeypatch する
（`test_visibility_audit.py` と同じ流儀）。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from api.routes.lecture_studio import topics as topics_module  # noqa: E402
from core.schema import AUDIT_ENTITY_COURSE_TOPIC, AUDIT_ENTITY_TYPES  # noqa: E402

USER = "11111111-1111-1111-1111-111111111111"
COURSE_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
TOPIC_ID = "t-1"


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    """1本目（所有者の SELECT）だけ返し、以降の UPDATE / DELETE は空を返す。"""

    def __init__(self, owner=USER):
        self._owner = owner
        self.statements: list[str] = []
        self.committed = 0
        self.closed = 0

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.statements.append(sql)
        if "SELECT user_id" in sql:
            return _Rows([(self._owner,)] if self._owner is not None else [])
        return _Rows([])

    def commit(self):
        self.committed += 1

    def rollback(self):
        pass

    def close(self):
        self.closed += 1


def _user(role: str = "TEACHER") -> dict:
    return {"id": USER, "username": "u", "email": "u@example.com", "role": role}


def _course_data(**topic_overrides) -> dict:
    topic = {"id": TOPIC_ID, "title": "トピック1"}
    topic.update(topic_overrides)
    return {"id": COURSE_ID, "title": "コース", "topics": [topic]}


@pytest.fixture
def recorded(monkeypatch):
    events: list[tuple] = []
    monkeypatch.setattr(
        topics_module,
        "record_review_event",
        lambda *args, **kwargs: events.append(args),
    )
    return events


@pytest.fixture
def session(monkeypatch):
    fake = _FakeSession()
    monkeypatch.setattr(topics_module, "_pg_session", lambda: fake)
    return fake


def _save(monkeypatch, body: dict, course_data: dict | None = None):
    data = course_data if course_data is not None else _course_data()
    monkeypatch.setattr(
        topics_module, "_course_data_for_studio", lambda cid, user: data
    )
    return topics_module.save_lecture_studio_course_topic(
        COURSE_ID, TOPIC_ID, body=body, current_user=_user(),
    )


class TestAuditVocabulary:
    def test_course_topic_entity_is_registered_in_the_catalog(self):
        assert AUDIT_ENTITY_COURSE_TOPIC == "course_topic"
        assert AUDIT_ENTITY_COURSE_TOPIC in AUDIT_ENTITY_TYPES


class TestTopicSaveIsAudited:
    def test_save_records_the_course_topic_and_actor(self, monkeypatch, session, recorded):
        out = _save(
            monkeypatch,
            {
                "student_material": {"source_text": "新しい教材本文"},
                "spoken_script": "読み上げ原稿",
            },
        )
        assert out == {"course_id": COURSE_ID, "topic_id": TOPIC_ID, "status": "edited"}
        assert len(recorded) == 1
        entity_type, entity_id, old_status, new_status, actor, metadata = recorded[0]
        assert entity_type == AUDIT_ENTITY_COURSE_TOPIC
        assert entity_id == COURSE_ID
        assert new_status == "edited"
        assert actor == USER
        assert metadata["action"] == "topic_draft_saved"
        assert metadata["topic_id"] == TOPIC_ID

    def test_changed_fields_are_listed_without_the_bodies(
        self, monkeypatch, session, recorded
    ):
        """本文は載せない（何が変わったかのフィールド名だけ）。"""
        import json

        _save(
            monkeypatch,
            {
                "student_material": {"source_text": "新しい教材本文"},
                "spoken_script": "読み上げ原稿",
                "cautions": ["注意"],
            },
        )
        metadata = recorded[0][5]
        assert set(metadata["changed_fields"]) == {
            "student_material", "spoken_script", "cautions",
        }
        payload = json.dumps(metadata, ensure_ascii=False)
        for body_text in ("新しい教材本文", "読み上げ原稿", "注意"):
            assert body_text not in payload

    def test_unchanged_fields_are_not_listed(self, monkeypatch, session, recorded):
        """同じ内容の再保存では changed_fields が空になる（起きていない変更を書かない）。"""
        existing = _course_data(
            student_material={"source_format": "eg-markdown-v1", "source_text": "同じ本文"},
            spoken_script="同じ原稿",
            key_concepts=[],
            cautions=[],
            check_questions=[],
        )
        _save(
            monkeypatch,
            {
                "student_material": {
                    "source_format": "eg-markdown-v1", "source_text": "同じ本文",
                },
                "spoken_script": "同じ原稿",
            },
            course_data=existing,
        )
        assert recorded[0][5]["changed_fields"] == []

    def test_audio_invalidation_side_effect_is_recorded(
        self, monkeypatch, session, recorded
    ):
        """保存が音声キャッシュを消す事実も残す（教員には保存前に告知される副作用）。"""
        _save(monkeypatch, {"spoken_script": "x"})
        assert recorded[0][5]["topic_audio_cache_invalidated"] is True
        assert any("DELETE FROM topic_lecture_audio_cache" in s for s in session.statements)

    def test_audit_comes_after_the_commit(self, monkeypatch, session, recorded):
        """ロールバックした保存を「保存した」と書かない。"""
        _save(monkeypatch, {"spoken_script": "x"})
        assert session.committed == 1
        assert len(recorded) == 1

    def test_nothing_is_recorded_when_the_topic_is_missing(
        self, monkeypatch, session, recorded
    ):
        monkeypatch.setattr(
            topics_module, "_course_data_for_studio", lambda cid, user: _course_data(id="other")
        )
        with pytest.raises(topics_module.HTTPException) as exc:
            topics_module.save_lecture_studio_course_topic(
                COURSE_ID, "no-such-topic", body={}, current_user=_user(),
            )
        assert exc.value.status_code == 404
        assert recorded == []

    def test_nothing_is_recorded_when_the_caller_is_not_the_owner(
        self, monkeypatch, recorded
    ):
        monkeypatch.setattr(
            topics_module, "_pg_session", lambda: _FakeSession(owner="99999999-9999-9999-9999-999999999999")
        )
        with pytest.raises(topics_module.HTTPException) as exc:
            _save(monkeypatch, {"spoken_script": "x"})
        assert exc.value.status_code == 403
        assert recorded == []

    def test_nothing_is_recorded_when_the_course_row_is_gone(self, monkeypatch, recorded):
        monkeypatch.setattr(topics_module, "_pg_session", lambda: _FakeSession(owner=None))
        with pytest.raises(topics_module.HTTPException) as exc:
            _save(monkeypatch, {"spoken_script": "x"})
        assert exc.value.status_code == 404
        assert recorded == []
