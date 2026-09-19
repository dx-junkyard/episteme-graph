"""``/chat/stream``（SSE）と ``/client-features`` の配線テスト（Phase 3-a §9 api）。

正本: ``docs/features/llm_response_streaming_design.md``（§2.2 / §3.4 / §3.5）。

``test_learning_stance_routing.py`` と同型の手法: DB・実 LLM には触れず、
``routes.learning`` の境界関数を monkeypatch で差し替えて route 関数を直接呼び、
**振る舞い**で固定する。

固定する事実:

- ``final`` は非ストリーム版 ``/chat`` の JSON と**キー集合・値とも一致**（ST7）
- ``final`` は ``LearningChatResponse`` の全フィールドを含む（間引かない）
- quota 超過は 1バイトも出さずに 429（ST2 — ``StreamingResponse`` に入る前）
- LLM 例外は 200 + ``final.degraded == true`` + 履歴保存1回（ST5 / I3）
- 中断（frame generator の ``close()``）では保存も痕跡も走らない（ST1 / O-1 裁定）
- フラグ off で 404（ST9）／ ``start.stance == final.stance``
"""

from __future__ import annotations

import asyncio
import json
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
import core.llm_policy as llm_policy_mod  # noqa: E402
from core.llm_worker.cost_gate import CostGate  # noqa: E402
from schemas import LearningChatRequest, LearningChatResponse  # noqa: E402


CURRENT_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "username": "student",
    "email": "student@test.local",
    "role": "STUDENT",
}


def _course_data() -> dict:
    return {
        "id": "course-1",
        "title": "テストコース",
        "domain": "テスト分野",
        "chapters": [],
        "topics": [{
            "id": "topic-1", "title": "テストトピック",
            "chapter_index": 0, "prerequisites": [],
        }],
        "concepts": [],
        "sources": [],
    }


def _fake_settings(**overrides) -> SimpleNamespace:
    base = dict(
        learning_chat_max_calls_per_day=300,
        learning_chat_streaming_enabled=True,
        learning_chat_llm_model="",
        llm_analysis_model="analysis-model-x",
        llm_fast_model="fast-model-x",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_body(message: str = "この定理の証明はどこから来ているのですか", **overrides):
    kwargs: dict = dict(message=message, history=[])
    kwargs.update(overrides)
    return LearningChatRequest(**kwargs)


@pytest.fixture
def chat_env(monkeypatch):
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
    monkeypatch.setattr(learning_mod, "list_visible_document_ids", lambda *a, **k: [])
    monkeypatch.setattr(learning_mod, "list_course_source_document_ids", lambda *a, **k: [])
    monkeypatch.setattr(learning_mod, "get_course_live_llm_models", lambda *a, **k: {})
    monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")

    persist_mock = MagicMock(return_value={"user_message_id": "msg-1"})
    monkeypatch.setattr(learning_mod, "persist_chat_history", persist_mock)
    trace_mock = MagicMock(return_value="trace-1")
    monkeypatch.setattr(learning_mod, "record_interest_trace", trace_mock)
    monkeypatch.setattr(learning_mod, "detect_and_record_misconception", MagicMock(return_value=None))

    return SimpleNamespace(
        settings=settings, persist_mock=persist_mock, trace_mock=trace_mock
    )


def _set_generation(monkeypatch, answer: str = "回答本体", pieces: list[str] | None = None):
    """非ストリーム / ストリームの両方の生成を同じ本文に固定する。"""
    monkeypatch.setattr(learning_mod, "generate_text", lambda **kwargs: answer)

    def _fake_stream(**kwargs):
        for piece in (pieces if pieces is not None else [answer]):
            yield piece

    monkeypatch.setattr(learning_mod, "generate_text_stream", _fake_stream)


def _collect(response) -> list[str]:
    async def _run():
        return [chunk async for chunk in response.body_iterator]

    return asyncio.run(_run())


def _frames(response) -> list[tuple[str, dict]]:
    raw = "".join(_collect(response))
    out: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        lines = block.split("\n")
        event = lines[0][len("event: "):]
        data = json.loads(lines[1][len("data: "):])
        out.append((event, data))
    return out


def _stream(course_id="course-1", topic_id="topic-1", body=None):
    return learning_mod.learning_chat_stream(
        course_id, topic_id, body or _make_body(), current_user=CURRENT_USER
    )


# ===========================================================================
# 1. ST7: final は非ストリーム版と同値
# ===========================================================================


class TestFinalMatchesTheJsonResponse:
    def test_final_equals_the_non_stream_response(self, chat_env, monkeypatch):
        _set_generation(monkeypatch, answer="回答本体", pieces=["回答", "本体"])

        plain = learning_mod.learning_chat(
            "course-1", "topic-1", _make_body(), current_user=CURRENT_USER
        )
        frames = _frames(_stream())

        final = [data for event, data in frames if event == "final"][0]
        expected = json.loads(json.dumps(plain.model_dump(), ensure_ascii=False, default=str))
        assert set(final) == set(expected)
        assert final == expected

    def test_final_contains_every_response_field(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        final = [d for e, d in _frames(_stream()) if e == "final"][0]

        assert set(LearningChatResponse.model_fields) <= set(final)

    def test_deltas_carry_only_the_text(self, chat_env, monkeypatch):
        _set_generation(monkeypatch, answer="ABCDE", pieces=["ABC", "DE"])

        frames = _frames(_stream())
        deltas = [d for e, d in frames if e == "delta"]

        assert deltas, "delta が1つも流れていない"
        for payload in deltas:
            assert set(payload) == {"t"}
        assert "".join(p["t"] for p in deltas) == "ABCDE"

    def test_event_order_is_start_deltas_final(self, chat_env, monkeypatch):
        _set_generation(monkeypatch, answer="あいうえお", pieces=["あい", "うえお"])

        events = [e for e, _ in _frames(_stream())]

        assert events[0] == "start"
        assert events[-1] == "final"
        assert set(events[1:-1]) == {"delta"}

    def test_start_stance_matches_final_stance(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        frames = _frames(_stream())
        start = [d for e, d in frames if e == "start"][0]
        final = [d for e, d in frames if e == "final"][0]

        assert start["stance"] == final["stance"]
        assert start["stance"] is not None

    def test_headers_disable_proxy_buffering(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        response = _stream()

        assert response.media_type == "text/event-stream"
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "no-cache"


# ===========================================================================
# 2. 衛生（ST4）
# ===========================================================================


class TestHygiene:
    def test_control_sequences_are_stripped_from_deltas(self, chat_env, monkeypatch):
        _set_generation(
            monkeypatch,
            answer="前\x1b[0m後",
            pieces=["前\x1b[0m", "後", "。" * 40],
        )

        deltas = [d["t"] for e, d in _frames(_stream()) if e == "delta"]

        joined = "".join(deltas)
        assert "\x1b" not in joined
        assert "[0m" not in joined
        assert joined.startswith("前後")


# ===========================================================================
# 3. ST2: quota は最初の1バイトより前
# ===========================================================================


class TestQuotaBeforeFirstByte:
    def test_quota_exceeded_returns_429_without_any_frame(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        chat_env.settings.learning_chat_max_calls_per_day = 0

        with pytest.raises(HTTPException) as excinfo:
            _stream()

        assert excinfo.value.status_code == 429
        # 事実文のみ（数値を出さない = ST8 / I2）。
        assert not any(ch.isdigit() for ch in str(excinfo.value.detail))

    def test_validation_error_is_a_normal_http_error(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)

        with pytest.raises(HTTPException) as excinfo:
            _stream(body=_make_body(intent_mode="discuss", discuss_scope="everything"))

        assert excinfo.value.status_code == 422


# ===========================================================================
# 4. ST5: 失敗は同じストリームの中で degraded に落ちる
# ===========================================================================


class TestDegradedInsideTheStream:
    def test_llm_failure_still_returns_200_and_persists_once(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "generate_text", lambda **k: "unused")

        def _boom(**kwargs):
            raise RuntimeError("upstream")
            yield  # pragma: no cover — generator にするため

        monkeypatch.setattr(learning_mod, "generate_text_stream", _boom)

        final = [d for e, d in _frames(_stream()) if e == "final"][0]

        assert final["degraded"] is True
        assert final["answer"] == learning_mod._CHAT_DEGRADED_MESSAGE
        assert chat_env.persist_mock.call_count == 1

    def test_partial_text_is_discarded_on_mid_stream_failure(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "generate_text", lambda **k: "unused")

        def _partial(**kwargs):
            yield "途中まで書いた"
            raise RuntimeError("upstream")

        monkeypatch.setattr(learning_mod, "generate_text_stream", _partial)

        frames = _frames(_stream())
        final = [d for e, d in frames if e == "final"][0]

        assert final["degraded"] is True
        assert "途中まで書いた" not in final["answer"]


# ===========================================================================
# 5. ST1 / O-1: 中断した往復は記録しない
# ===========================================================================


class TestAbortedTurnIsNotRecorded:
    def test_close_mid_stream_skips_persistence_and_traces(self, chat_env, monkeypatch):
        _set_generation(monkeypatch, answer="あいうえお", pieces=["あい", "うえ", "お"])

        gen = learning_mod._learning_chat_core(
            "course-1", "topic-1", _make_body(), CURRENT_USER, stream=True
        )
        first = next(gen)
        frames = learning_mod._sse_frames(gen, first)
        assert next(frames).startswith("event: start")
        frames.close()

        assert chat_env.persist_mock.call_count == 0
        assert chat_env.trace_mock.call_count == 0


# ===========================================================================
# 6. ST9: フラグと配布
# ===========================================================================


class TestFeatureFlag:
    def test_disabled_flag_returns_404(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        chat_env.settings.learning_chat_streaming_enabled = False

        with pytest.raises(HTTPException) as excinfo:
            _stream()

        assert excinfo.value.status_code == 404

    def test_client_features_returns_a_single_boolean(self, chat_env):
        payload = learning_mod.learning_client_features(current_user=CURRENT_USER)

        assert set(payload) == {"chat_streaming"}
        assert payload["chat_streaming"] is True

    def test_client_features_mirrors_the_setting(self, chat_env):
        chat_env.settings.learning_chat_streaming_enabled = False

        assert learning_mod.learning_client_features(
            current_user=CURRENT_USER
        ) == {"chat_streaming": False}


# ===========================================================================
# 7. LLM 非経由の確定応答（§3.4 手順3）
# ===========================================================================


class TestNonLlmTurnsUseTheSameClientPath:
    def test_typed_usage_help_streams_start_and_final_only(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        monkeypatch.setattr(learning_mod, "_search_manual", None)
        monkeypatch.setattr(learning_mod, "_vector_search_manual", None)

        frames = _frames(_stream(body=_make_body("使い方を教えて", support_action="usage_help")))
        events = [e for e, _ in frames]

        assert events == ["start", "final"]
        # 様相が解決できない経路は null のまま（捏造しない）。
        assert frames[0][1]["stance"] == frames[1][1]["stance"]
