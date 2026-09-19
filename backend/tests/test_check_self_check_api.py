"""確認問題の並置 `/check` と自己確認 `/check/self-check` のルートテスト（是正 F1）。

正本: `docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md` §4 第1波 #1
（出所は `six_lenses_2026-09-10/01_learner.md` 提案2 / `05_ai.md` 提案1）。

ここで固定するのは
  - `/check` は完了を書かない（`record_topic_check_pass` を呼ばない）・合否を返さない
  - LLM 例外時は 200 + `degraded=true` + 固定文（文字数フォールバックで判定しない）
  - 完了を書けるのは `/check/self-check` の agreed / disagreed だけ
  - `verdict_wrong` は記録するが完了させない（かつ進行を止めない）
  - 語彙外の self_check は 422、受講外コース・不明トピックは 404

手法は test_learning_chat_infra.py と同型（DB・実 LLM には触れず、``routes.learning``
の境界関数を monkeypatch し、対象関数を FastAPI を経由せず直接呼び出す）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as learning_mod  # noqa: E402
from schemas import (  # noqa: E402
    LearningCheckQuestionRequest,
    LearningCheckSelfCheckRequest,
)

CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "role": "STUDENT",
}


def _course_data() -> dict:
    return {
        "id": "course-1",
        "title": "テストコース",
        "chapters": [],
        "topics": [{
            "id": "topic-1",
            "title": "テストトピック",
            "content": "教材本文",
            "check_questions": [{
                "question": "要点を説明してください。",
                "answer_requirements": ["定義を述べる", "関係を述べる"],
                "model_answer": "解答例本文",
                "explanation": "解説本文",
            }],
        }],
    }


@pytest.fixture
def env(monkeypatch):
    """コース取得・完了現況・LLM 呼び出しの境界だけ差し替える。"""
    calls: dict = {"record": [], "completion": []}

    monkeypatch.setattr(learning_mod, "get_course_data", lambda uid, cid: _course_data())
    monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda cid: {})

    def _completion(uid, cid, cd):
        calls["completion"].append((uid, cid))
        return {"course_completed": False, "completed_topic_ids": []}

    def _record(uid, cid, tid, cd):
        calls["record"].append((uid, cid, tid))
        return {
            "topic_completed": True,
            "course_completed": True,
            "completed_topic_ids": [tid],
        }

    monkeypatch.setattr(learning_mod, "get_course_completion", _completion)
    monkeypatch.setattr(learning_mod, "record_topic_check_pass", _record)
    return calls


def _observation_json() -> str:
    return (
        '{"observations": ['
        '{"requirement": "定義を述べる", "status": "covered", "statement": "触れているようです"},'
        '{"requirement": "関係を述べる", "status": "not_mentioned", "statement": "見当たらないようです"}'
        '], "model_answer": "解答例", "explanation": "解説"}'
    )


# ---------------------------------------------------------------------------
# /check — 並置のみ
# ---------------------------------------------------------------------------


class TestCheckReturnsJuxtapositionOnly:
    def test_response_has_no_verdict_field(self, env, monkeypatch):
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kw: _observation_json())

        resp = learning_mod.check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckQuestionRequest(answer="私の答え"),
            current_user=CURRENT_USER,
        )

        assert "passed" not in resp.model_dump()
        assert resp.advisory is True
        assert resp.self_check_required is True

    def test_observations_are_projected_and_split(self, env, monkeypatch):
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kw: _observation_json())

        resp = learning_mod.check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckQuestionRequest(answer="私の答え"),
            current_user=CURRENT_USER,
        )

        assert resp.covered == ["定義を述べる"]
        assert resp.not_mentioned == ["関係を述べる"]
        assert [o.status for o in resp.observations] == ["covered", "not_mentioned"]
        assert resp.model_answer == "解答例"
        assert resp.explanation == "解説"
        assert resp.answer_requirements == ["定義を述べる", "関係を述べる"]
        assert any("私の答え" in s for s in resp.statements)

    def test_check_never_records_completion(self, env, monkeypatch):
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kw: _observation_json())

        learning_mod.check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckQuestionRequest(answer="私の答え"),
            current_user=CURRENT_USER,
        )

        assert env["record"] == []
        assert env["completion"], "現況の読み出しは行う"

    def test_fabricated_requirement_is_not_presented_as_a_requirement(self, env, monkeypatch):
        monkeypatch.setattr(
            learning_mod, "generate_text",
            lambda **kw: '{"observations": [{"requirement": "存在しない要件", '
                         '"status": "not_mentioned", "statement": "見当たらないようです"}]}',
        )

        resp = learning_mod.check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckQuestionRequest(answer="私の答え"),
            current_user=CURRENT_USER,
        )

        assert resp.not_mentioned == []
        assert resp.observations[0].requirement == ""
        assert resp.observations[0].statement == "見当たらないようです"

    def test_unknown_course_and_topic_are_404(self, env, monkeypatch):
        monkeypatch.setattr(learning_mod, "get_course_data", lambda uid, cid: None)
        with pytest.raises(HTTPException) as exc:
            learning_mod.check_topic_understanding(
                "course-x", "topic-1",
                LearningCheckQuestionRequest(answer="a"), current_user=CURRENT_USER,
            )
        assert exc.value.status_code == 404

        monkeypatch.setattr(learning_mod, "get_course_data", lambda uid, cid: _course_data())
        with pytest.raises(HTTPException) as exc:
            learning_mod.check_topic_understanding(
                "course-1", "topic-zzz",
                LearningCheckQuestionRequest(answer="a"), current_user=CURRENT_USER,
            )
        assert exc.value.status_code == 404


class TestCheckDegradation:
    def test_llm_failure_degrades_without_a_verdict(self, env, monkeypatch):
        def _boom(**kwargs):
            raise RuntimeError("llm down")

        monkeypatch.setattr(learning_mod, "generate_text", _boom)

        resp = learning_mod.check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckQuestionRequest(answer="短い"),
            current_user=CURRENT_USER,
        )

        assert resp.degraded is True
        assert resp.statements == [
            "AI の観点提示ができませんでした。出題の要件と自分の回答を見比べてください。"
        ]
        assert resp.observations == []
        assert resp.answer_requirements == ["定義を述べる", "関係を述べる"]
        assert resp.model_answer == "解答例本文"
        assert env["record"] == []

    def test_long_answer_does_not_pass_on_degradation(self, env, monkeypatch):
        """旧実装の「40字以上なら合格」を復活させない。"""
        def _boom(**kwargs):
            raise RuntimeError("llm down")

        monkeypatch.setattr(learning_mod, "generate_text", _boom)

        resp = learning_mod.check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckQuestionRequest(answer="あ" * 400),
            current_user=CURRENT_USER,
        )

        assert resp.topic_completed is False
        assert env["record"] == []

    def test_completion_read_failure_does_not_break_the_response(self, env, monkeypatch):
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kw: _observation_json())

        def _boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(learning_mod, "get_course_completion", _boom)

        resp = learning_mod.check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckQuestionRequest(answer="私の答え"),
            current_user=CURRENT_USER,
        )

        assert resp.course_completed is False
        assert resp.completed_topic_ids == []
        assert resp.statements


# ---------------------------------------------------------------------------
# /check/self-check — 完了を書ける唯一の経路
# ---------------------------------------------------------------------------


class TestSelfCheck:
    @pytest.mark.parametrize("value", ["agreed", "disagreed"])
    def test_advancing_values_record_completion(self, env, value):
        resp = learning_mod.self_check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckSelfCheckRequest(self_check=value),
            current_user=CURRENT_USER,
        )

        assert resp.self_check == value
        assert resp.topic_completed is True
        assert resp.course_completed is True
        assert resp.completed_topic_ids == ["topic-1"]
        assert env["record"] == [(CURRENT_USER["id"], "course-1", "topic-1")]

    def test_verdict_wrong_records_nothing_and_does_not_complete(self, env):
        resp = learning_mod.self_check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckSelfCheckRequest(self_check="verdict_wrong"),
            current_user=CURRENT_USER,
        )

        assert resp.topic_completed is False
        assert env["record"] == []
        assert env["completion"], "現況だけは読む"

    def test_verdict_wrong_does_not_block_a_later_advance(self, env):
        learning_mod.self_check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckSelfCheckRequest(self_check="verdict_wrong"),
            current_user=CURRENT_USER,
        )
        resp = learning_mod.self_check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckSelfCheckRequest(self_check="agreed"),
            current_user=CURRENT_USER,
        )
        assert resp.topic_completed is True

    @pytest.mark.parametrize("value", ["", "passed", "PASS", "agree", "ok"])
    def test_invalid_value_is_422(self, env, value):
        with pytest.raises(HTTPException) as exc:
            learning_mod.self_check_topic_understanding(
                "course-1", "topic-1",
                LearningCheckSelfCheckRequest(self_check=value),
                current_user=CURRENT_USER,
            )
        assert exc.value.status_code == 422
        assert env["record"] == []

    def test_invalid_value_is_rejected_before_the_course_lookup(self, env, monkeypatch):
        def _boom(uid, cid):
            raise AssertionError("course lookup must not run for an invalid value")

        monkeypatch.setattr(learning_mod, "get_course_data", _boom)
        with pytest.raises(HTTPException) as exc:
            learning_mod.self_check_topic_understanding(
                "course-1", "topic-1",
                LearningCheckSelfCheckRequest(self_check="nope"),
                current_user=CURRENT_USER,
            )
        assert exc.value.status_code == 422

    def test_inaccessible_course_is_404(self, env, monkeypatch):
        monkeypatch.setattr(learning_mod, "get_course_data", lambda uid, cid: None)
        with pytest.raises(HTTPException) as exc:
            learning_mod.self_check_topic_understanding(
                "course-1", "topic-1",
                LearningCheckSelfCheckRequest(self_check="agreed"),
                current_user=CURRENT_USER,
            )
        assert exc.value.status_code == 404
        assert env["record"] == []

    def test_unknown_topic_is_404(self, env):
        with pytest.raises(HTTPException) as exc:
            learning_mod.self_check_topic_understanding(
                "course-1", "topic-zzz",
                LearningCheckSelfCheckRequest(self_check="agreed"),
                current_user=CURRENT_USER,
            )
        assert exc.value.status_code == 404
        assert env["record"] == []

    def test_persistence_failure_does_not_break_the_response(self, env, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(learning_mod, "record_topic_check_pass", _boom)
        resp = learning_mod.self_check_topic_understanding(
            "course-1", "topic-1",
            LearningCheckSelfCheckRequest(self_check="agreed"),
            current_user=CURRENT_USER,
        )
        assert resp.self_check == "agreed"
        assert resp.topic_completed is False
