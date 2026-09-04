"""学生 HELP ルート（設計 docs/features/manual_help_kb_design.md §1-3 / §4-3 / §4-4）
のテスト。

対象: ``backend/api/routes/learning.py`` の ``_is_usage_question`` /
``_usage_help_response`` / ``learning_chat`` 内の pre-route 挿入、
``backend/api/schemas.py`` の ``LearningChatResponse.manual_citations``、
``backend/api/services.py`` の ``_INTEREST_KINDS`` / ``get_interest_traces`` 除外。

§4-4（Phase 2 分離リリース）: ``_classify_intent`` が4番目のラベル ``USAGE_HELP`` を
返した場合、``learning_chat`` は pre-route（保守的キーワード判定）と同じ HELP ハンドラ
（``_usage_help_response``）へ委譲する（クラス ``TestClassifierRoutedUsageHelp``）。

§4-3（学生側バックエンド）: ``LearningChatRequest.screen_mode`` を
``search_manual(..., screen=...)`` へそのまま橋渡しする（クラス ``TestScreenModeBridging``）。

``core.help_kb.search_manual`` は並行実装中のため、``routes.learning._search_manual``
を monkeypatch して契約 (``search_manual(query, *, audience, limit=3, screen=None) ->
list[{file, anchor, title, body, audience, citation, documented}]``) 相当のダミーを
差し込む（DB・実 LLM には触れず、既存テストファイル群と同型の手法）。
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
from schemas import LearningChatRequest  # noqa: E402


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


def _make_body(message: str = "画面の使い方を教えて", **overrides) -> LearningChatRequest:
    kwargs: dict = dict(message=message, history=[])
    kwargs.update(overrides)
    return LearningChatRequest(**kwargs)


_HIT = {
    "file": "getting_started.md",
    "anchor": "voice-mode",
    "title": "音声モードの使い方",
    "body": "入力欄横のマイクボタンを押すと音声入力が始まります。",
    "audience": "student",
    "citation": "manual/student/getting_started.md#voice-mode",
    "documented": True,
}

_NOT_DOCUMENTED_HIT = {**_HIT, "documented": False}


@pytest.fixture
def help_env(monkeypatch):
    """learning_chat() をフル実行できるよう DB/LLM/検索の境界だけモックする。"""
    settings = _fake_settings()
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())
    monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())

    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    trace_mock = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)

    def _unexpected_classify_intent(*a, **k):
        raise AssertionError("HELP pre-route ヒット時に _classify_intent が呼ばれてはならない")

    def _unexpected_check_prerequisites(*a, **k):
        raise AssertionError("HELP pre-route ヒット時に check_prerequisites が呼ばれてはならない")

    monkeypatch.setattr(learning_mod, "_classify_intent", _unexpected_classify_intent)
    monkeypatch.setattr(learning_mod, "check_prerequisites", _unexpected_check_prerequisites)

    # ベクトル補助層フォールバック（Phase 3 ①）はデフォルトで無ヒットに固定する。
    # 未モックのまま放置すると実 DB/実 LLM を呼びに行きかねない
    # （このテストファイルは _search_manual 境界のみを対象にしているため）。
    # 個々のテストでベクトルヒットを検証したい場合は明示的に上書きすること。
    monkeypatch.setattr(learning_mod, "_vector_search_manual", lambda *a, **k: [])

    return SimpleNamespace(settings=settings, persist_mock=persist_mock, trace_mock=trace_mock)


def _set_search_manual(monkeypatch, hits: list[dict] | Exception | None):
    if isinstance(hits, Exception):
        def _raise(*a, **k):
            raise hits
        monkeypatch.setattr(learning_mod, "_search_manual", _raise)
    else:
        monkeypatch.setattr(learning_mod, "_search_manual", lambda *a, **k: hits)


# ===========================================================================
# 1. _is_usage_question — 保守的キーワード判定
# ===========================================================================


class TestIsUsageQuestion:
    @pytest.mark.parametrize("message", [
        "この機能ってどう使うの？",
        "音声モードの使い方がわからない",
        "マイクの使い方を教えて",
        "使い方を教えて",
        "このボタンはどこにありますか",
        "アプリの操作方法がわかりません",
    ])
    def test_true_cases(self, message):
        assert learning_mod._is_usage_question(message) is True

    @pytest.mark.parametrize("message", [
        "この式はどう使うの",
        "運動方程式の使い方",
        "エネルギー保存則とは何ですか",
        "この定理の証明方法は?",
        "量子力学について教えてください",
        "こんにちは",
        "",
        "a" * 60,  # 長すぎるメッセージは保守的に偽
    ])
    def test_false_cases(self, message):
        assert learning_mod._is_usage_question(message) is False

    def test_typed_action_maps_to_usage_help(self):
        assert learning_mod._route_for_typed_action("usage_help") == "USAGE_HELP"


# ===========================================================================
# 2. pre-route 挿入位置 — casual バイパス・意図分類・前提知識チェックより手前
# ===========================================================================


class TestPreRouteEarlyReturn:
    def test_typed_action_hits_help_route_without_intent_classification(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [_HIT])
        body = _make_body(message="適当な文章", support_action="usage_help")

        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert _HIT["body"] in resp.answer
        assert resp.manual_citations == [{
            "file": _HIT["file"], "anchor": _HIT["anchor"], "title": _HIT["title"],
        }]

    def test_keyword_match_hits_help_route(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [_HIT])
        body = _make_body(message="音声モードの使い方がわからない")

        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert _HIT["body"] in resp.answer

    def test_atlas_context_takes_precedence_over_help_route(self, help_env, monkeypatch):
        """atlas_context 付きのリクエストは HELP pre-route の対象外
        （↗ アクションと競合させないための設計上の除外）。"""
        _set_search_manual(monkeypatch, [_HIT])
        monkeypatch.setattr(
            learning_mod, "_atlas_action_response",
            lambda *a, **k: learning_mod.LearningChatResponse(answer="atlas応答", course_update=None),
        )
        body = _make_body(
            message="使い方を教えて",
            atlas_context={"action": "mind", "node_id": "n1"},
        )

        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert resp.answer == "atlas応答"


# ===========================================================================
# 3. テキスト経路: 本文素通し + quota 非消費
# ===========================================================================


class TestTextRoutePassthrough:
    def test_hit_text_route_is_verbatim_with_citation_and_footer(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [_HIT])

        def _unexpected_generate_text(**kwargs):
            raise AssertionError("テキスト経路で generate_text が呼ばれてはならない（パラフレーズ禁止）")

        monkeypatch.setattr(learning_mod, "generate_text", _unexpected_generate_text)

        body = _make_body(support_action="usage_help")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert _HIT["body"] in resp.answer
        assert "[出典1]" in resp.answer
        assert "教材の内容についての質問なら、そのまま送り直してください。" in resp.answer
        assert resp.degraded is False

    def test_hit_text_route_does_not_consume_quota(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [_HIT])
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: "呼ばれてはならない")
        help_env.settings.learning_chat_max_calls_per_day = 1

        body = _make_body(support_action="usage_help")
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        key = (today_str(), CURRENT_USER["id"])
        assert learning_mod._learning_chat_cost_gate.daily_counts.get(key, 0) == 0

    def test_hit_text_route_persists_history(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [_HIT])
        body = _make_body(support_action="usage_help")
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)
        help_env.persist_mock.assert_called_once()


# ===========================================================================
# 4. 無ヒット / 未整備 — LLM 非呼び出し・固定文
# ===========================================================================


class TestNoHitOrNotDocumented:
    def test_no_hit_returns_fixed_text_without_llm(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [])

        def _unexpected_generate_text(**kwargs):
            raise AssertionError("無ヒット時に generate_text が呼ばれてはならない")

        monkeypatch.setattr(learning_mod, "generate_text", _unexpected_generate_text)

        body = _make_body(support_action="usage_help")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert "その使い方の説明はまだ整備されていません。" in resp.answer
        assert resp.manual_citations is None

    def test_not_documented_returns_fixed_text_without_llm(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [_NOT_DOCUMENTED_HIT])

        def _unexpected_generate_text(**kwargs):
            raise AssertionError("未整備時に generate_text が呼ばれてはならない")

        monkeypatch.setattr(learning_mod, "generate_text", _unexpected_generate_text)

        body = _make_body(support_action="usage_help")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert "その使い方の説明はまだ整備されていません。" in resp.answer

    def test_search_manual_exception_falls_back_to_no_hit(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, RuntimeError("kb boom"))
        body = _make_body(support_action="usage_help")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)
        assert "その使い方の説明はまだ整備されていません。" in resp.answer

    def test_search_manual_module_absent_falls_back_to_no_hit(self, help_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "_search_manual", None)
        body = _make_body(support_action="usage_help")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)
        assert "その使い方の説明はまだ整備されていません。" in resp.answer

    def test_no_hit_does_not_consume_quota(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [])
        help_env.settings.learning_chat_max_calls_per_day = 1

        body = _make_body(support_action="usage_help")
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        key = (today_str(), CURRENT_USER["id"])
        assert learning_mod._learning_chat_cost_gate.daily_counts.get(key, 0) == 0


# ===========================================================================
# 5. casual/音声経路 — 1 LLM コール + フェイルソフト
# ===========================================================================


class TestCasualRoute:
    def test_casual_hit_calls_llm_once_and_consumes_quota(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [_HIT])
        captured: dict = {}

        def _fake_generate_text(**kwargs):
            captured.update(kwargs)
            return "マイクを押すと話せますよ。"

        monkeypatch.setattr(learning_mod, "generate_text", _fake_generate_text)

        body = _make_body(message="音声モードの使い方教えて", intent_mode="casual")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert "マイクを押すと話せますよ。" in resp.answer
        assert resp.degraded is False
        key = (today_str(), CURRENT_USER["id"])
        assert learning_mod._learning_chat_cost_gate.daily_counts.get(key, 0) == 1

    def test_casual_hit_llm_failure_falls_back_to_raw_body_degraded(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [_HIT])

        def _raise(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(learning_mod, "generate_text", _raise)

        body = _make_body(message="音声モードの使い方教えて", intent_mode="casual")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert _HIT["body"] in resp.answer
        assert resp.degraded is True

    def test_casual_no_hit_does_not_call_llm(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [])

        def _unexpected_generate_text(**kwargs):
            raise AssertionError("casual でも無ヒット時は generate_text を呼ばない")

        monkeypatch.setattr(learning_mod, "generate_text", _unexpected_generate_text)

        body = _make_body(message="音声モードの使い方教えて", intent_mode="casual")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert "その使い方の説明はまだ整備されていません。" in resp.answer


# ===========================================================================
# 6. interest_traces 記録 — 逐語を積まない (P3)
# ===========================================================================


class TestInterestTraceRecording:
    def test_hit_trace_uses_title_not_verbatim_message(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [_HIT])
        body = _make_body(message="この超長い質問文の逐語がpayloadに残ってはいけない", support_action="usage_help")

        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        help_env.trace_mock.assert_called_once()
        _, kwargs = help_env.trace_mock.call_args
        args = help_env.trace_mock.call_args.args
        # record_interest_trace(user_id, course_id, topic_id, kind=..., text=..., extra_payload=...)
        called_kwargs = help_env.trace_mock.call_args.kwargs
        assert called_kwargs.get("kind") == "help_usage"
        assert called_kwargs.get("text") == _HIT["title"]
        assert "この超長い質問文の逐語" not in called_kwargs.get("text", "")
        payload = called_kwargs.get("extra_payload")
        assert payload == {"help_anchor": _HIT["citation"], "documented": True, "no_hit": False}

    def test_no_hit_trace_payload_is_minimal(self, help_env, monkeypatch):
        _set_search_manual(monkeypatch, [])
        body = _make_body(support_action="usage_help")

        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        called_kwargs = help_env.trace_mock.call_args.kwargs
        assert called_kwargs.get("kind") == "help_usage"
        payload = called_kwargs.get("extra_payload")
        assert payload == {"help_anchor": None, "documented": False, "no_hit": True}


# ===========================================================================
# 7. CHIT_CHAT 再誘導 + services.py の変更点
# ===========================================================================


class TestChitChatReguidanceAndServicesChanges:
    def test_chit_chat_mentions_usage_help(self):
        source = Path(learning_mod.__file__).read_text(encoding="utf-8")
        idx = source.find('if intent == "CHIT_CHAT":')
        assert idx > 0
        block = source[idx: idx + 600]
        assert "画面の使い方についての質問にもお答えできます" in block

    def test_interest_kinds_includes_help_usage(self):
        import services

        assert "help_usage" in services._INTEREST_KINDS

    def test_get_interest_traces_excludes_help_usage(self):
        import inspect
        import services

        src = inspect.getsource(services.get_interest_traces)
        assert "kind <> 'help_usage'" in src

    def test_learning_chat_response_has_manual_citations_field(self):
        from schemas import LearningChatResponse

        resp = LearningChatResponse(answer="ok")
        assert resp.manual_citations is None


# ===========================================================================
# 8. 意図分類 USAGE_HELP（設計 §4-4, Phase 2 分離リリース）
# ===========================================================================


@pytest.fixture
def classifier_help_env(monkeypatch):
    """pre-route（保守的キーワード判定）をすり抜け、意図分類 LLM 経由で USAGE_HELP に
    到達するケースを厳密にテストするための環境。``_is_usage_question`` を False 固定に
    することで、pre-route ではなく分類ルートだけを通っていることを保証する。"""
    settings = _fake_settings()
    monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())
    monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
    monkeypatch.setattr(learning_mod, "_is_usage_question", lambda message, **kwargs: False)

    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    trace_mock = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)

    def _unexpected_check_prerequisites(*a, **k):
        raise AssertionError("USAGE_HELP 判定後は check_prerequisites が呼ばれてはならない")

    monkeypatch.setattr(learning_mod, "check_prerequisites", _unexpected_check_prerequisites)
    monkeypatch.setattr(learning_mod, "_vector_search_manual", lambda *a, **k: [])

    return SimpleNamespace(settings=settings, persist_mock=persist_mock, trace_mock=trace_mock)


class TestClassifierRoutedUsageHelp:
    """①分類が USAGE_HELP を返したら HELP ハンドラに到達する（テキスト経路・quota 非消費）。"""

    def test_llm_classifies_usage_help_routes_to_handler(self, classifier_help_env, monkeypatch):
        _set_search_manual(monkeypatch, [_HIT])
        classify_mock = MagicMock(return_value="USAGE_HELP")
        monkeypatch.setattr(learning_mod, "_classify_intent", classify_mock)

        # pre-route の保守的キーワード判定はすり抜ける想定のメッセージ（_is_usage_question
        # は fixture で False 固定済みなので、内容自体は任意でよい）。
        body = _make_body(message="これはどこで確認すればいいですか")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        classify_mock.assert_called_once()
        assert _HIT["body"] in resp.answer
        assert "[出典1]" in resp.answer
        assert "教材の内容についての質問なら、そのまま送り直してください。" in resp.answer

    def test_llm_classifies_usage_help_text_route_does_not_consume_quota(
        self, classifier_help_env, monkeypatch
    ):
        """分類そのもの（generate_text 呼び出し）はモックで置き換えているため、
        HELP ハンドラのテキスト経路自体が quota を消費しないことを確認する。"""
        _set_search_manual(monkeypatch, [_HIT])
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "USAGE_HELP")
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: "呼ばれてはならない")
        classifier_help_env.settings.learning_chat_max_calls_per_day = 1

        body = _make_body(message="これはどこで確認すればいいですか")
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        key = (today_str(), CURRENT_USER["id"])
        assert learning_mod._learning_chat_cost_gate.daily_counts.get(key, 0) == 0

    def test_classification_result_other_than_usage_help_does_not_reach_help_handler(
        self, monkeypatch
    ):
        """②分類失敗時は（内部フォールバックである）DOMAIN_RAG に落ち、
        HELP ハンドラには到達しない（従来どおりの経路を維持）。"""
        settings = _fake_settings()
        monkeypatch.setattr(learning_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(llm_policy_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(learning_mod, "_learning_chat_cost_gate", CostGate())
        monkeypatch.setattr(learning_mod, "get_course_data", lambda user_id, course_id: _course_data())
        monkeypatch.setattr(learning_mod, "_is_usage_question", lambda message, **kwargs: False)
        monkeypatch.setattr(learning_mod, "search_chunks_with_metadata", lambda *a, **k: [])
        monkeypatch.setattr(learning_mod, "log_unanswered_query", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "check_prerequisites", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "_atlas_topic_attribution", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "check_and_count_confirm_prompt", lambda *a, **k: False)
        monkeypatch.setattr(learning_mod, "maybe_schedule_tension_mining", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "maybe_schedule_anchor_mining", lambda *a, **k: None)
        monkeypatch.setattr(learning_mod, "persist_chat_history", MagicMock(return_value={"user_message_id": "msg-1"}))
        monkeypatch.setattr(learning_mod, "record_interest_trace", MagicMock(return_value="trace-1"))
        monkeypatch.setattr(learning_mod, "detect_and_record_misconception", MagicMock(return_value=None))
        monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: "テスト応答")
        # 分類 LLM 呼び出しが失敗すると _classify_intent は内部で DOMAIN_RAG を返す
        # （test_intent_routing.py::test_llm_failure_defaults_to_domain_rag で単体検証済み）。
        # ここでは学習チャット側がその結果を尊重し、HELP ハンドラを迂回することを検証する。
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")

        def _unexpected_usage_help(*a, **k):
            raise AssertionError("USAGE_HELP 以外の分類結果で _usage_help_response が呼ばれてはならない")

        monkeypatch.setattr(learning_mod, "_usage_help_response", _unexpected_usage_help)
        _set_search_manual(monkeypatch, [_HIT])  # 呼ばれないはずだが、呼ばれても無害にしておく

        body = _make_body(message="これはどこで確認すればいいですか")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert resp.answer  # DOMAIN_RAG の通常経路が完走し応答が返る


# ===========================================================================
# 9. screen_mode コンテキスト絞り込み（設計 §4-3）
# ===========================================================================


class TestScreenModeBridging:
    def test_screen_mode_is_passed_to_search_manual(self, help_env, monkeypatch):
        captured: dict = {}

        def _fake_search_manual(query, *, audience, limit=3, screen=None):
            captured["audience"] = audience
            captured["screen"] = screen
            return [_HIT]

        monkeypatch.setattr(learning_mod, "_search_manual", _fake_search_manual)

        body = _make_body(support_action="usage_help", screen_mode="lecture")
        learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert captured["screen"] == "lecture"
        assert captured["audience"] == "student"

    def test_screen_mode_unset_passes_none_and_keeps_legacy_behavior(self, help_env, monkeypatch):
        captured: dict = {}

        def _fake_search_manual(query, *, audience, limit=3, screen=None):
            captured["screen"] = screen
            return [_HIT]

        monkeypatch.setattr(learning_mod, "_search_manual", _fake_search_manual)

        body = _make_body(support_action="usage_help")
        resp = learning_mod.learning_chat("course-1", "topic-1", body, current_user=CURRENT_USER)

        assert captured["screen"] is None
        assert _HIT["body"] in resp.answer
