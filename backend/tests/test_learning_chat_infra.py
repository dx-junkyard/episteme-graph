"""チャット型 AI 支援の共通基盤整理 Phase 2-B（学習チャット）の検証（正本:
docs/features/assistant_common_infra_design.md §1 / §2-2 / §3 / §4 / §7）。

対象: ``backend/api/routes/learning.py`` の ``learning_chat`` /
``_generate_graph_element_explanation`` / ``check_topic_understanding``。

  1. コスト上限（CostGate day-only, キー=(today, user_id)）— 超過時 429・事実文のみ（I2）。
  2. LLM を1度も呼ばないパス（承認済みグラフ要素タップ）は消費しない。
  3. 会話履歴ウィンドウ化（window_history）— 学習チャット本体は20件、グラフ要素説明は6件。
  4. モデル解決（resolve_model）— 学習チャット本体 generate_text への配線。
  5. 縮退規約（degraded）— LLM 例外時に 200 + degraded=true + 固定文、履歴は保存、
     誤解検出・ドリルダウンはスキップ。
  6. usage_context 補完 — check_topic_understanding の generate_text 呼び出しが
     "learning:understanding_check" で帰属すること。

test_assistant_infra_unification.py（Phase 2-E, 同一設計書の他サブタスク実装）と
同型の手法: DB・実 LLM には触れず、``routes.learning`` モジュールの境界関数を
monkeypatch で差し替えたうえで、対象関数を FastAPI を経由せず直接呼び出す
（test_intent_routing.py 等、本ファイル群の既存パターンを踏襲）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.learning as learning_mod  # noqa: E402
import core.llm_worker.client as llm_worker_client  # noqa: E402
import core.llm_policy as llm_policy_mod  # noqa: E402
from core.llm_worker.cost_gate import CostGate, today_str  # noqa: E402
from schemas import LearningChatRequest, LearningCheckQuestionRequest  # noqa: E402


CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}


def _course_data(**topic_overrides) -> dict:
    topic = {
        "id": "topic-1",
        "title": "テストトピック",
        "chapter_index": 0,
        "prerequisites": [],
    }
    topic.update(topic_overrides)
    return {
        "id": "course-1",
        "title": "テストコース",
        "domain": "テスト分野",
        "chapters": [],
        "topics": [topic],
        "concepts": [],
        "sources": [],
    }


def _fake_settings(**overrides) -> SimpleNamespace:
    base = dict(
        learning_chat_max_calls_per_day=300,
        learning_chat_llm_model="",
        llm_analysis_model="analysis-model-x",
        llm_fast_model="fast-model-x",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_body(message: str = "テストの質問です", history: list | None = None, **overrides) -> LearningChatRequest:
    kwargs: dict = dict(message=message, history=history or [], support_action="ask_question")
    kwargs.update(overrides)
    return LearningChatRequest(**kwargs)


@pytest.fixture
def chat_env(monkeypatch):
    """learning_chat() をフル実行できるよう DB/LLM 境界だけモックする共有フィクスチャ。

    ``support_action="ask_question"`` により意図分類 (_classify_intent) は LLM を
    経由せず DOMAIN_RAG に確定するため、本体 RAG の generate_text 呼び出しだけが
    このリクエストで唯一の LLM 呼び出しになる。
    """
    settings = _fake_settings()
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_policy_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())

    monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
    monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
    monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_prerequisites", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
    monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
    monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)

    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    trace_mock = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)
    misconception_mock = MagicMock(return_value=None)
    monkeypatch.setattr(learning_mod, "detect_and_record_misconception", misconception_mock)

    return SimpleNamespace(
        settings=settings,
        persist_mock=persist_mock,
        trace_mock=trace_mock,
        misconception_mock=misconception_mock,
    )


# ===========================================================================
# 1. コスト上限（§1） — 超過時 429・事実文のみ（I2）
# ===========================================================================


class TestLearningChatCostGate:
    def test_quota_exhausted_raises_429_without_numbers(self, chat_env, monkeypatch):
        """上限に達している状態でリクエストすると、LLM を呼ぶ前に 429 で止まる。

        detail は事実文のみで、残数・上限値などの数値を一切含まない（I2）。
        """
        chat_env.settings.learning_chat_max_calls_per_day = 1
        key = (today_str(), CURRENT_USER["id"])
        learning_mod._learning_chat_cost_gate.daily_counts[key] = 1  # 既に上限到達

        def _unexpected_generate_text(**kwargs):
            raise AssertionError("quota 超過時に generate_text が呼ばれてはならない")

        monkeypatch.setattr(learning_mod, "generate_text", _unexpected_generate_text)

        body = _make_body()
        with pytest.raises(HTTPException) as exc_info:
            learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert exc_info.value.status_code == 429
        detail = str(exc_info.value.detail)
        assert not any(ch.isdigit() for ch in detail), f"detail に数値が含まれている: {detail!r}"

    def test_quota_recovers_under_limit(self, chat_env, monkeypatch):
        """上限未満なら通常どおり応答が返る（ゲートの誤検知でないことの確認）。"""
        chat_env.settings.learning_chat_max_calls_per_day = 5
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: "テスト応答")

        body = _make_body()
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)
        assert resp.degraded is False

    def test_voice_and_history_endpoints_are_not_gated(self):
        """音声 transcribe/speak・履歴 GET/DELETE・check_topic_understanding は
        学習チャットのコスト上限の対象外（設計書 §1）— ソース上、これらのハンドラ内に
        _consume_learning_chat_quota の呼び出しが無いことを確認する。"""
        import inspect

        for fn in (
            learning_mod.voice_transcribe_route,
            learning_mod.voice_speak_route,
            learning_mod.get_chat_history,
            learning_mod.delete_chat_history,
            learning_mod.delete_chat_message_from,
            learning_mod.check_topic_understanding,
        ):
            src = inspect.getsource(fn)
            assert "_consume_learning_chat_quota" not in src, f"{fn.__name__} が誤ってゲート対象になっている"


# ===========================================================================
# 2. LLM を1度も呼ばないパスは消費しない
# ===========================================================================


class TestQuotaNotConsumedWithoutLLM:
    def test_approved_graph_element_answer_never_invokes_on_llm_call(self, monkeypatch):
        """承認済み element_explanations があるグラフ要素タップは、ローカル LLM 生成を
        スキップするため on_llm_call（=コスト消費）も一切呼ばれない（設計書 §1 の例示）。"""
        from core import element_explanations

        monkeypatch.setattr(
            learning_mod,
            "get_graph_element_context",
            lambda *a, **k: {
                "chunk_id": "chunk-1",
                "chunk_text": "本文",
                "formulas": [],
                "material_id": "mat-1",
                "source_title": "論文タイトル",
                "instructor_id": None,
                "element_id": "eq_1",
                "element_type": "formula",
                "element_label": "この数式",
                "target_formula": {"id": "eq_1", "latex": "E=mc^2", "is_display": True},
                "target_citation": None,
                "target_reference": None,
                "graph_description": "",
                "related_chunks": [],
            },
        )
        monkeypatch.setattr(learning_mod, "_resolve_course_document_ids", lambda *a, **k: ["doc-1"])
        monkeypatch.setattr(learning_mod, "record_student_stumble_event", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "persist_chat_history", lambda *a, **k: {"user_message_id": "m1"})

        ref = (element_explanations.ELEMENT_TYPE_EQUATION, "eq_1")
        monkeypatch.setattr(
            element_explanations,
            "approved_for_elements",
            lambda session, doc_id, refs: {
                ref: [{
                    "kind": element_explanations.KIND_CONTEXTUAL,
                    "body": "承認済みの文脈説明。",
                    "status": element_explanations.STATUS_APPROVED,
                }],
            },
        )

        on_llm_call = MagicMock()

        def _unexpected_generate_text(**kwargs):
            raise AssertionError("承認済み回答パスで generate_text が呼ばれてはならない")

        monkeypatch.setattr(learning_mod, "generate_text", _unexpected_generate_text)

        body = LearningChatRequest(
            message="", chunk_id="chunk-1", element_id="eq_1", element_type="formula",
        )
        resp = learning_mod._generate_graph_element_explanation(
            user_id="u1", course_id="c1", topic_id="t1",
            course_title="コース", topic_title="トピック",
            course_data={}, body=body, on_llm_call=on_llm_call,
        )

        assert "承認済みの文脈説明。" in resp.answer
        on_llm_call.assert_not_called()


# ===========================================================================
# 3. 会話履歴ウィンドウ化（§2-2）
# ===========================================================================


class TestHistoryWindowing:
    def test_main_chat_history_capped_to_20_messages(self, chat_env, monkeypatch):
        """本体RAGの generate_text に渡る messages は、固定の3件(system/user/assistant)
        + 直近20件の履歴 + 最新のユーザー発言1件、の合計24件に制限される。"""
        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return "テスト応答"

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        long_history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"メッセージ{i}"}
            for i in range(50)
        ]
        body = _make_body(message="最新の質問", history=long_history)
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        messages = captured["messages"]
        assert len(messages) == 3 + 20 + 1
        history_slice = messages[3:-1]
        history_contents = {m["content"] for m in history_slice}
        assert history_contents == {f"メッセージ{i}" for i in range(30, 50)}
        assert history_slice[0]["content"] == "メッセージ30"
        assert history_slice[-1]["content"] == "メッセージ49"
        assert messages[-1]["content"] == "最新の質問"

    def test_graph_element_explanation_history_capped_to_6_messages(self, monkeypatch):
        """グラフ要素説明の recent_history は直近6件へウィンドウ化される
        （現行 -6 件の挙動をユーティリティ経由へ置換, ほぼ同一挙動）。"""
        monkeypatch.setattr(
            learning_mod,
            "get_graph_element_context",
            lambda *a, **k: {
                "chunk_id": "chunk-1",
                "chunk_text": "本文",
                "formulas": [],
                "material_id": "mat-1",
                "source_title": "論文タイトル",
                "instructor_id": None,
                "element_id": "heavy_quark_limit",
                "element_type": "concept",
                "element_label": "重いクォーク極限",
                "target_formula": None,
                "target_citation": None,
                "target_reference": None,
                "graph_description": "",
                "related_chunks": [{"source_title": "論文", "text": "関連文"}],
            },
        )
        monkeypatch.setattr(learning_mod, "record_student_stumble_event", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "persist_chat_history", lambda *a, **k: {"user_message_id": "m1"})
        monkeypatch.setattr(learning_mod, "get_personal_layer", lambda *a, **k: {})

        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return "説明します"

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        long_history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"履歴{i}"}
            for i in range(20)
        ]
        body = LearningChatRequest(
            message="", chunk_id="chunk-1", element_id="heavy_quark_limit", element_type="concept",
            history=long_history,
        )
        learning_mod._generate_graph_element_explanation(
            user_id="u1", course_id="c1", topic_id="t1",
            course_title="コース", topic_title="トピック",
            course_data={}, body=body,
        )

        prompt = captured["messages"][0]["content"]
        assert "履歴19" in prompt
        assert "履歴14" in prompt
        assert "履歴13" not in prompt
        assert "履歴0" not in prompt


# ===========================================================================
# 4. モデル解決（§3）
# ===========================================================================


class TestModelResolution:
    def test_resolve_model_falls_back_to_analysis_when_env_unset(self):
        """core.llm_worker.client.resolve_model への直接検証: 空文字設定は
        analysis tier（llm_analysis_model）に解決される（I1: 現行挙動を保存）。"""
        from unittest.mock import patch

        settings = _fake_settings(learning_chat_llm_model="", llm_analysis_model="o3-mini")
        with patch("core.llm_policy.get_settings", return_value=settings):
            assert (
                llm_worker_client.resolve_model("learning_chat_llm_model", fallback="analysis")
                == "o3-mini"
            )

    def test_main_chat_passes_resolved_model_to_generate_text(self, chat_env, monkeypatch):
        """学習チャット本体の generate_text 呼び出しに、resolve_model の解決結果が
        model= として渡っていること。env 未設定なら llm_analysis_model と一致する。"""
        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return "テスト応答"

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        body = _make_body()
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert captured["model"] == chat_env.settings.llm_analysis_model

    def test_main_chat_uses_explicit_env_model_when_set(self, chat_env, monkeypatch):
        chat_env.settings.learning_chat_llm_model = "custom-learning-model"
        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return "テスト応答"

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        body = _make_body()
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert captured["model"] == "custom-learning-model"


# ===========================================================================
# 5. 縮退規約（§4） — 会話は死なせない（I3）
# ===========================================================================


class TestDegradedFallback:
    def test_llm_exception_degrades_to_200_with_fixed_answer(self, chat_env, monkeypatch):
        def _raise(**kwargs):
            raise RuntimeError("LLM boom")

        monkeypatch.setattr(learning_mod, "generate_text", _raise)

        body = _make_body(message="量子力学について教えてください")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert resp.degraded is True
        assert resp.answer == "AI 応答を生成できませんでした。しばらくしてからもう一度お試しください。"

    def test_degraded_turn_persists_history(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

        body = _make_body(message="質問です")
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        chat_env.persist_mock.assert_called_once()

    def test_degraded_turn_skips_misconception_detection(self, chat_env, monkeypatch):
        # わざと誤解検出キーワードを含む固定文にはならないはずだが、万一のためにも
        # detect_and_record_misconception が一切呼ばれないことを直接検証する。
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

        body = _make_body(message="訂正してほしいです")
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        chat_env.misconception_mock.assert_not_called()

    def test_healthy_turn_is_not_degraded(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: "正常な応答です")

        body = _make_body()
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert resp.degraded is False
        assert "正常な応答です" in resp.answer


# ===========================================================================
# 6. usage_context 補完（§7）
# ===========================================================================


class TestUnderstandingCheckUsageContext:
    def test_generate_text_call_is_attributed_to_understanding_check(self, monkeypatch):
        from core.llm_usage.context import current_usage_context

        monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
        monkeypatch.setattr(
            learning_mod,
            "record_topic_check_pass",
            lambda *a, **k: {"topic_completed": True, "course_completed": False, "completed_topic_ids": []},
        )

        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured["feature"] = current_usage_context().feature
            return '{"passed": true, "feedback": "ok", "model_answer": "ans", "explanation": ""}'

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        body = LearningCheckQuestionRequest(answer="十分に具体的な回答をここに書きます" * 3)
        resp = learning_mod.check_topic_understanding(
            "course-1", "topic-1", body, current_user=CURRENT_USER,
        )

        assert captured["feature"] == "learning:understanding_check"
        assert resp.passed is True

    def test_feature_registered_in_known_features(self):
        from core.llm_usage.schema import KNOWN_FEATURES

        assert "learning:understanding_check" in KNOWN_FEATURES
