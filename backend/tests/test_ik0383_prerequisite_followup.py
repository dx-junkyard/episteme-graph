"""IK-0383: 前提確認の逆質問を繰り返さず、答え・問いを前提の説明へ流す。

ペルソナ通し受講 第 8 周: 逆質問「DCF法…を理解していますか」のあと
「いいえ、DCF法から教えてください」と打つと同じ逆質問が返り（3回続いた）、楽屋での
「DCF法とは何ですか」も逆質問に吸い込まれた。原因は `check_prerequisites` の言及判定が
前提の**名前全体**（「DCF法による磁場強度の推定」）の部分文字列一致しか見ないことと、
楽屋が前提ゲートを素通りしていたこと。ルート側で決定論に判定し、説明の3段解決（是正 F4）へ
流す。「理解している」の記帳は従来どおり `check_prerequisites` だけが行う。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as learning_mod  # noqa: E402
import core.llm_policy as llm_policy_mod  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from schemas import LearningChatRequest  # noqa: E402

CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}
TOPIC_TITLE = "主結果：フィードバックに形づくられた磁化フィラメント"
PREREQ = "DCF法による磁場強度の推定"


def _gate_text() -> str:
    """services.check_prerequisites が組み立てる逆質問（文言は services が正本）。"""
    return (
        f"「{TOPIC_TITLE}」を理解するには、まず以下の前提知識を押さえる必要があります：\n\n"
        f"**{PREREQ}**\n\n"
        f"この前提知識を理解していますか？\n"
        f"理解している場合は、その前提で「{TOPIC_TITLE}」の説明に進みます。"
        f"理解できていない場合は、まず「{PREREQ}」から説明します。\n\n"
        f"（下の選択肢から進め方を選んでください。）"
    )


def _course_data():
    return {
        "id": "course-1",
        "title": "テストコース",
        "domain": "テスト分野",
        "chapters": [],
        "concepts": [],
        "sources": [{"material_id": "mat-course"}],
        "topics": [
            {
                "id": "t3",
                "title": TOPIC_TITLE,
                "chapter_index": 0,
                "prerequisites": [{"name": PREREQ}],
            }
        ],
    }


def _topic_info():
    return _course_data()["topics"][0]


@pytest.fixture
def chat_env(monkeypatch):
    settings = SimpleNamespace(
        learning_chat_max_calls_per_day=300,
        learning_chat_llm_model="",
        llm_analysis_model="analysis-model-x",
        llm_fast_model="fast-model-x",
    )
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_policy_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())
    monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
    monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
    monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda uid: [])
    monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
    monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "detect_and_record_misconception", MagicMock(return_value=None))
    trace_mock = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)
    monkeypatch.setattr(learning_mod, "prejudge_stance_route", lambda *a, **k: "DOMAIN_RAG")

    gate_calls: list[str] = []

    def _gate(user_id, course_id, course_data, topic_title, message):
        gate_calls.append(message)
        return {"message": _gate_text(), "first_prerequisite": PREREQ, "unlearned": [PREREQ]}

    monkeypatch.setattr(learning_mod, "check_prerequisites", _gate)
    prompts: list[str] = []

    def _generate_text(**kwargs):
        prompts.append(" ".join(str(m.get("content")) for m in kwargs.get("messages") or []))
        return "説明本文"

    monkeypatch.setattr(learning_mod, "generate_text", _generate_text)
    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    return SimpleNamespace(gate_calls=gate_calls, prompts=prompts, trace_mock=trace_mock)


def _chat(message: str, history=None, **extra):
    body = LearningChatRequest(message=message, history=history or [], **extra)
    return learning_mod.learning_chat("course-1", "t3", body, current_user=CURRENT_USER)


def _gate_history():
    return [
        {"role": "user", "content": "主結果を教えて"},
        {"role": "assistant", "content": _gate_text()},
    ]


class TestGateMarkerMatchesServices:
    def test_services_gate_text_contains_the_marker(self, monkeypatch):
        """ルートの逆質問判定と services の介入文が同じ定型部分を持つこと（片方だけ変えない）。"""
        from api import services

        class _Session:
            def execute(self, *a, **k):
                return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])

            def close(self):
                pass

        monkeypatch.setattr(services, "_pg_session", lambda: _Session())
        out = services.check_prerequisites("u", "c", _course_data(), TOPIC_TITLE, "主結果を教えて")
        assert out is not None
        assert learning_mod.PREREQUISITE_GATE_MARKER in out["message"]
        assert learning_mod._previous_turn_was_prerequisite_gate(
            [{"role": "assistant", "content": out["message"]}], TOPIC_TITLE
        ) == PREREQ


class TestFollowupClassification:
    def _run(self, message, history=None):
        return learning_mod._prerequisite_followup(
            message, history, _topic_info(), TOPIC_TITLE, _course_data()
        )

    def test_typed_negative_after_gate_is_explain(self):
        assert self._run("いいえ、DCF法から教えてください", _gate_history()) == ("explain", PREREQ)

    def test_bare_negative_after_gate_explains_the_gated_prerequisite(self):
        assert self._run("いいえ", _gate_history()) == ("explain", PREREQ)

    def test_acknowledgement_after_gate_is_not_explain(self):
        assert self._run("はい、理解しています", _gate_history()) == ("after_gate", None)

    def test_unrelated_message_after_gate_does_not_regate(self):
        assert self._run("出典はどこですか", _gate_history()) == ("after_gate", None)

    def test_question_about_the_prerequisite_is_explain_without_gate(self):
        assert self._run("DCF法とは何ですか") == ("explain", PREREQ)

    def test_mention_without_request_is_left_alone(self):
        assert self._run("DCF法で求めた値は何μGですか") == (None, None)

    def test_gate_for_another_topic_is_not_this_gate(self):
        other = [{"role": "assistant", "content": _gate_text().replace(TOPIC_TITLE, "別の題")}]
        assert self._run("出典はどこですか", other) == (None, None)


class TestRoute:
    def test_typed_negative_answer_gets_an_explanation_not_the_gate(self, chat_env, monkeypatch):
        def _unexpected(*a, **k):
            raise AssertionError("前提の説明要求で意図分類の LLM を呼ばない")

        monkeypatch.setattr(learning_mod, "_classify_intent", _unexpected)
        resp = _chat("いいえ、DCF法から教えてください", _gate_history())
        assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
        assert resp.support_mode == "prerequisite_review"
        # 3段解決: 資料が無ければ model_generated + 閉世界の事実文（是正 F4）。
        assert resp.content_grounding == "model_generated"
        assert learning_mod.PREREQUISITE_CLOSED_WORLD_FACT in resp.answer
        assert f"「{PREREQ}」" in resp.answer
        # ナビゲーターの案内ではなく、その前提の説明を書かせるプロンプト。
        assert f"前提知識「{PREREQ}」そのものを" in chat_env.prompts[-1]
        assert "具体的な解説（数式展開など）はまだ行わないこと" not in chat_env.prompts[-1]

    def test_question_about_prerequisite_is_answered(self, chat_env):
        resp = _chat("DCF法とは何ですか")
        assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
        assert resp.content_grounding == "model_generated"

    def test_gate_is_never_repeated_consecutively(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")
        resp = _chat("出典はどこですか", _gate_history())
        # check_prerequisites は呼ぶ（記帳の責務）が、介入は捨てる。
        assert chat_env.gate_calls == ["出典はどこですか"]
        assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
        assert resp.answer.endswith("説明本文")

    def test_first_gate_still_appears(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")
        resp = _chat("主結果を教えて")
        assert learning_mod.PREREQUISITE_GATE_MARKER in resp.answer

    def test_backstage_is_not_gated(self, chat_env):
        resp = _chat(
            "初歩的な質問ですみません。DCF法は何を測って磁場の強さを出す方法なんですか？",
            backstage=True,
        )
        assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
        # 楽屋の記録面（kind='backstage_question'）は従来どおり。
        assert chat_env.trace_mock.call_args.kwargs["kind"] == "backstage_question"

    def test_typed_button_keeps_precedence(self, chat_env):
        """「いいえ」ボタン（drilldown）は従来どおり DOMAIN_RAG（UI の明示が勝つ）。"""
        resp = _chat(
            f"{PREREQ}について教えてください", _gate_history(), support_action="drilldown",
        )
        assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
        # 通常の RAG 回答（前提の説明ルートへは奪わない）。
        assert resp.answer.endswith("説明本文")
        assert f"前提知識「{PREREQ}」そのものを" not in chat_env.prompts[-1]
