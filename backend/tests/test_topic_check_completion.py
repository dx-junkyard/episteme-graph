"""コース完了判定のサーバー正本化（レビュー指摘1・P1）のテスト。

対象:
  - ``backend/api/services.py::record_topic_check_pass`` / ``get_course_completion``
    / ``calculate_progress``
  - ``backend/api/routes/learning.py::check_topic_understanding``（フェイルオープン配線の静的検証）
  - ``backend/api/schemas.py::LearningCheckQuestionResponse`` / ``LearningProgress``

test_prerequisite_routing.py と同型の手法（DB 不要のフェイクセッションで
``services._pg_session`` を差し替える）で、learning_states.progress_data への
upsert・コース完了状態の毎回導出（保存値を信用しない）を検証する。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))


# ---------------------------------------------------------------------------
# フェイクセッション（test_prerequisite_routing.py と同型）。
#
# ``learning_states.progress_data`` の状態を保持し、SQL 文の部分一致で
# SELECT/UPDATE を素朴にエミュレートする。その他の SQL（INSERT ... ON CONFLICT /
# personal_graph / learning_chat_history の日付集計等）は無害な
# 空結果を返す。
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        if self._row is None:
            return []
        if isinstance(self._row, list):
            return self._row
        return [self._row]


class _FakeSession:
    def __init__(self, progress_data: dict | None = None):
        self.progress_data: dict = dict(progress_data or {})
        self.calls: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        params = dict(params or {})
        self.calls.append((sql, params))

        if "SELECT progress_data FROM learning_states" in sql:
            return _Result((self.progress_data,))

        if "UPDATE learning_states" in sql and "progress_data" in sql:
            raw = params.get("progress")
            if isinstance(raw, str):
                self.progress_data = json.loads(raw)
            elif raw is not None:
                self.progress_data = raw
            return _Result(None)

        # INSERT ... ON CONFLICT DO NOTHING / personal_graph / chat history /
        # 本テストの関心外の SQL は無害な空結果でよい。
        return _Result(None)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _course_data(topic_ids):
    return {"topics": [{"id": tid, "title": tid} for tid in topic_ids]}


# ===========================================================================
# _course_completed_from_topic_ids（純粋関数）
# ===========================================================================


class TestCourseCompletedDerivation:
    def test_empty_topics_never_completed(self):
        from api import services

        assert services._course_completed_from_topic_ids([], {}) is False

    def test_all_present_is_completed(self):
        from api import services

        assert services._course_completed_from_topic_ids(
            ["t1", "t2"], {"t1": "x", "t2": "y"}
        ) is True

    def test_partial_is_not_completed(self):
        from api import services

        assert services._course_completed_from_topic_ids(
            ["t1", "t2"], {"t1": "x"}
        ) is False


# ===========================================================================
# record_topic_check_pass
# ===========================================================================


class TestRecordTopicCheckPass:
    def test_pass_records_completed_topic_with_timestamp(self, monkeypatch):
        from api import services

        fake = _FakeSession()
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.record_topic_check_pass(
            "user-1", "course-1", "t1", _course_data(["t1", "t2"]),
        )

        assert result["topic_completed"] is True
        assert result["course_completed"] is False
        assert "t1" in fake.progress_data["completed_topics"]
        assert fake.progress_data["completed_topics"]["t1"]  # ISO タイムスタンプが入っている
        assert fake.committed is True

    def test_does_not_record_on_untouched_topics(self, monkeypatch):
        """t2 に合格していないので completed_topics に t2 は現れない。"""
        from api import services

        fake = _FakeSession()
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        services.record_topic_check_pass(
            "user-1", "course-1", "t1", _course_data(["t1", "t2"]),
        )

        assert "t2" not in fake.progress_data["completed_topics"]

    def test_all_topics_completed_sets_course_completed(self, monkeypatch):
        from api import services

        fake = _FakeSession()
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        services.record_topic_check_pass(
            "user-1", "course-1", "t1", _course_data(["t1", "t2"]),
        )
        result = services.record_topic_check_pass(
            "user-1", "course-1", "t2", _course_data(["t1", "t2"]),
        )

        assert result["course_completed"] is True
        assert sorted(result["completed_topic_ids"]) == ["t1", "t2"]
        assert fake.progress_data.get("course_completed_at")

    def test_jumping_to_final_topic_does_not_complete_course(self, monkeypatch):
        """最終トピックへ直接飛んだだけでは course_completed=True にしない。"""
        from api import services

        fake = _FakeSession()
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.record_topic_check_pass(
            "user-1", "course-1", "t2", _course_data(["t1", "t2", "t3"]),
        )

        assert result["topic_completed"] is True
        assert result["course_completed"] is False

    def test_repeated_pass_does_not_overwrite_timestamp(self, monkeypatch):
        from api import services

        fake = _FakeSession()
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        services.record_topic_check_pass(
            "user-1", "course-1", "t1", _course_data(["t1", "t2"]),
        )
        first_ts = fake.progress_data["completed_topics"]["t1"]

        services.record_topic_check_pass(
            "user-1", "course-1", "t1", _course_data(["t1", "t2"]),
        )
        second_ts = fake.progress_data["completed_topics"]["t1"]

        assert first_ts == second_ts

    def test_course_completed_at_not_overwritten_once_set(self, monkeypatch):
        from api import services

        fake = _FakeSession()
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        services.record_topic_check_pass("user-1", "course-1", "t1", _course_data(["t1"]))
        first_completed_at = fake.progress_data["course_completed_at"]

        # 同じトピックにもう一度合格しても course_completed_at は変わらない。
        services.record_topic_check_pass("user-1", "course-1", "t1", _course_data(["t1"]))
        second_completed_at = fake.progress_data["course_completed_at"]

        assert first_completed_at == second_completed_at


# ===========================================================================
# get_course_completion（読み取り専用・毎回導出）
# ===========================================================================


class TestGetCourseCompletion:
    def test_derives_true_when_all_current_topics_completed(self, monkeypatch):
        from api import services

        fake = _FakeSession(progress_data={
            "completed_topics": {"t1": "2026-01-01T00:00:00+00:00", "t2": "2026-01-01T00:00:01+00:00"},
        })
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.get_course_completion("user-1", "course-1", _course_data(["t1", "t2"]))

        assert result["course_completed"] is True
        assert sorted(result["completed_topic_ids"]) == ["t1", "t2"]

    def test_does_not_trust_stored_course_completed_at_when_topics_added(self, monkeypatch):
        """完了後にコースへトピックが追加されたら course_completed=False に戻る（導出）。"""
        from api import services

        fake = _FakeSession(progress_data={
            "completed_topics": {"t1": "2026-01-01T00:00:00+00:00", "t2": "2026-01-01T00:00:01+00:00"},
            "course_completed_at": "2026-01-01T00:00:01+00:00",
        })
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        # コース側に t3 が新たに追加された。
        result = services.get_course_completion(
            "user-1", "course-1", _course_data(["t1", "t2", "t3"]),
        )

        assert result["course_completed"] is False

    def test_read_only_does_not_mutate_or_commit(self, monkeypatch):
        from api import services

        fake = _FakeSession(progress_data={"completed_topics": {"t1": "x"}})
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        services.get_course_completion("user-1", "course-1", _course_data(["t1"]))

        assert fake.committed is False
        assert not any("UPDATE" in sql for sql, _ in fake.calls)
        assert not any("INSERT" in sql for sql, _ in fake.calls)

    def test_no_progress_row_is_not_completed(self, monkeypatch):
        from api import services

        fake = _FakeSession(progress_data={})
        monkeypatch.setattr(services, "_pg_session", lambda: fake)

        result = services.get_course_completion("user-1", "course-1", _course_data(["t1", "t2"]))

        assert result["course_completed"] is False
        assert result["completed_topic_ids"] == []


# ===========================================================================
# calculate_progress との統合
# ===========================================================================


class TestCalculateProgressIncludesCompletion:
    def test_returns_completed_topic_ids_and_course_completed(self, monkeypatch):
        from api import services

        fake = _FakeSession(progress_data={
            "completed_topics": {"t1": "2026-01-01T00:00:00+00:00", "t2": "2026-01-01T00:00:01+00:00"},
        })
        monkeypatch.setattr(services, "_pg_session", lambda: fake)
        monkeypatch.setattr(
            services, "get_personal_layer",
            lambda user_id, course_id: {"misconceptions_by_topic": {}, "chat_anchors": {}},
        )

        progress = services.calculate_progress(
            "user-1", "course-1", _course_data(["t1", "t2"]),
        )

        assert progress["course_completed"] is True
        assert sorted(progress["completed_topic_ids"]) == ["t1", "t2"]

    def test_partial_progress_is_not_course_completed(self, monkeypatch):
        from api import services

        fake = _FakeSession(progress_data={
            "completed_topics": {"t1": "2026-01-01T00:00:00+00:00"},
        })
        monkeypatch.setattr(services, "_pg_session", lambda: fake)
        monkeypatch.setattr(
            services, "get_personal_layer",
            lambda user_id, course_id: {"misconceptions_by_topic": {}, "chat_anchors": {}},
        )

        progress = services.calculate_progress(
            "user-1", "course-1", _course_data(["t1", "t2"]),
        )

        assert progress["course_completed"] is False
        assert progress["completed_topic_ids"] == ["t1"]


# ===========================================================================
# スキーマ: 新規フィールドのデフォルト・受け渡し
# ===========================================================================


class TestSchemaFields:
    def test_check_question_response_defaults(self):
        from schemas import LearningCheckQuestionResponse

        resp = LearningCheckQuestionResponse(passed=True, feedback="ok")
        assert resp.topic_completed is False
        assert resp.course_completed is False
        assert resp.completed_topic_ids == []

    def test_check_question_response_with_completion(self):
        from schemas import LearningCheckQuestionResponse

        resp = LearningCheckQuestionResponse(
            passed=True,
            feedback="ok",
            topic_completed=True,
            course_completed=True,
            completed_topic_ids=["t1", "t2"],
        )
        assert resp.topic_completed is True
        assert resp.course_completed is True
        assert resp.completed_topic_ids == ["t1", "t2"]

    def test_learning_progress_defaults(self):
        from schemas import LearningProgress

        progress = LearningProgress()
        assert progress.completed_topic_ids == []
        assert progress.course_completed is False

    def test_learning_progress_accepts_calculate_progress_shape(self):
        """calculate_progress() の返り値 dict をそのまま LearningProgress(**progress) に
        展開できること（routes/learning.py::get_progress の実際の使い方）。"""
        from schemas import LearningProgress

        progress_dict = {
            "learning_concepts": 1,
            "misconceptions": 0,
            "sessions": [],
            "completed_topic_ids": ["t1"],
            "course_completed": False,
        }
        progress = LearningProgress(**progress_dict)
        assert progress.completed_topic_ids == ["t1"]
        assert progress.course_completed is False


# ===========================================================================
# ルート: フェイルオープン配線の静的検証
# ===========================================================================


class TestCheckTopicUnderstandingRouteWiring:
    """backend/api/routes/learning.py::check_topic_understanding のソース検証。

    永続化の失敗で採点レスポンス自体を落とさない（fail-open）ことと、
    合格/不合格それぞれで正しいサービス関数を呼ぶことを検証する。
    """

    @staticmethod
    def _route_source():
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "api" / "routes" / "learning.py"
        src = path.read_text(encoding="utf-8")
        start = src.index("def check_topic_understanding")
        tail = src[start + 1:]
        rel_end = tail.find("\n@router")
        end = start + 1 + rel_end if rel_end != -1 else len(src)
        return src[start:end]

    def test_calls_record_on_pass(self):
        body = self._route_source()
        assert "record_topic_check_pass(" in body

    def test_calls_get_course_completion_on_fail(self):
        body = self._route_source()
        assert "get_course_completion(" in body

    def test_persistence_wrapped_in_try_except(self):
        body = self._route_source()
        assert "try:" in body
        assert "except Exception:" in body
        assert "logger.warning" in body

    def test_response_construction_is_outside_try_block(self):
        """LearningCheckQuestionResponse(...) の呼び出しは永続化用 try/except の外にあり、
        永続化例外がレスポンス自体を落とさないこと（return は最後の except の後）。"""
        body = self._route_source()
        response_idx = body.index("return LearningCheckQuestionResponse(")
        # 採点用 LLM 呼び出しの try/except と、永続化用の try/except の最低2ブロックがある。
        assert body.count("try:") >= 2
        last_except_idx = body.rindex("except Exception:")
        assert response_idx > last_except_idx
