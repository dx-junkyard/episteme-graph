"""学習者の進捗・問いの見出しの読み時変換（IK-0416・ペルソナ通し受講 第 8 周）。

- 進捗の学習履歴に予約疑似トピック id（``_discussion``）をそのまま出さない
- コースのソース論文に直付けした議論（``_doc:{id}``）も論文の題名付きで並べる
- 日付は日本時間（UTC の暦日で前日に見せない）
- 引っかかり候補（tension digest）の context_label も読み時に表示名へ（行は書き換えない）
- 誤解の数は本人が確定した誤解メモだけ（是正 F5 を崩さない — AI 検出の記録は数えない）
"""

from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from api import services  # noqa: E402


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, history_rows, title_rows=()):
        self.history_rows = history_rows
        self.title_rows = list(title_rows)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, dict(params or {})))
        if "FROM learning_chat_history" in sql:
            return _Result(self.history_rows)
        if "FROM documents WHERE id::text = ANY(:ids)" in sql:
            return _Result(self.title_rows)
        return _Result([])

    def close(self):
        pass


def _patch(monkeypatch, session, doc_ids=("doc-1",)):
    monkeypatch.setattr(services, "_pg_session", lambda: session)
    monkeypatch.setattr(
        services, "get_personal_layer", lambda *_a, **_k: {"misconceptions_by_topic": {}}
    )
    monkeypatch.setattr(
        services, "get_course_completion",
        lambda *_a, **_k: {"completed_topic_ids": [], "course_completed": False},
    )
    monkeypatch.setattr(services, "list_course_source_document_ids", lambda _cd: set(doc_ids))


def test_reserved_topic_and_document_discuss_sessions_are_labelled(monkeypatch):
    utc = dt.timezone.utc
    rows = [
        ("t1", [{}, {}], dt.datetime(2026, 9, 27, 21, 50, tzinfo=utc), "course-1"),
        ("_discussion", [{}], dt.datetime(2026, 9, 27, 21, 40, tzinfo=utc), "course-1"),
        ("_discussion", [{}], dt.datetime(2026, 9, 27, 22, 0, tzinfo=utc), "_doc:doc-1"),
    ]
    session = _Session(rows, title_rows=[("doc-1", "Cep B の磁場")])
    _patch(monkeypatch, session)
    course = {"topics": [{"id": "t1", "title": "問題設定"}]}
    progress = services.calculate_progress("u-1", "course-1", course)

    topics = [s["topic"] for s in progress["sessions"]]
    assert topics == ["問題設定", "論文との議論", "論文との議論（コース外）: Cep B の磁場"]
    assert "_discussion" not in str(progress)
    # 21:50 UTC は日本時間では翌日の 6:50
    assert progress["sessions"][0]["date"] == "9/28"
    sql, params = next(c for c in session.calls if "FROM learning_chat_history" in c[0])
    assert params["doc_context_ids"] == ["_doc:doc-1"]


def test_misconception_count_stays_confirmed_only(monkeypatch):
    """是正 F5: 進捗の誤解の数は本人確定のみ（台帳の AI 検出記録とは数え方が違う）。"""
    session = _Session([])
    _patch(monkeypatch, session)
    monkeypatch.setattr(
        services, "get_personal_layer",
        lambda *_a, **_k: {"misconceptions_by_topic": {"t1": [
            {"status": "candidate"}, {"status": "confirmed"},
        ]}},
    )
    assert services.calculate_progress("u-1", "c-1", {"topics": []})["misconceptions"] == 1


def test_learner_date_label_is_jst():
    utc = dt.timezone.utc
    assert services.learner_date_label(dt.datetime(2026, 9, 27, 14, 59, tzinfo=utc)) == "9/27"
    assert services.learner_date_label(dt.datetime(2026, 9, 27, 15, 0, tzinfo=utc)) == "9/28"
    assert services.learner_date_label(None) == ""


def test_tension_digest_context_label_translated_at_read_time():
    assert services._learner_context_label("course-1", "_discussion") == "論文との議論"
    assert services._learner_context_label("_doc:x", "_discussion") == "論文との議論（コース外）"
    assert services._learner_context_label("course-1", "第1章 · 問題設定") == "第1章 · 問題設定"
