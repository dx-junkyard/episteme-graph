"""``core.llm.generate_text_stream`` の挙動テスト（ストリーミング Phase 3-a §9 core）。

正本: ``docs/features/llm_response_streaming_design.md``（§3.1 / §3.2 / §6）。

固定する事実:

- delta は本文だけ（usage を伴う最終 chunk は表示に流さない）
- 観測（``observe_chat`` → ``UsageEvent``）は**必ず1回**。正常終了・例外・
  呼び出し側の ``close()``（中断）のいずれでも1件（ST3 / U2）
- 実測 usage が取れれば ``usage_source="reported"``、取れなければ ``estimated_*``
  （U1 の分離集計をそのまま通す）
- ``operation`` は ``"chat"`` のままで、ストリームである事実は ``metadata.streamed``
- 非 openai プロバイダは例外を出さず1 delta にフォールバックする（原則8）
- U層帰属は値渡し（``usage_ctx``）で、contextvar を yield を跨いで開かない（§3.2）
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import core.llm as llm_mod  # noqa: E402
import core.llm_usage.observe as observe_mod  # noqa: E402


def _delta_chunk(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))], usage=None
    )


def _usage_chunk(prompt=11, completion=7):
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            prompt_tokens_details=None,
            completion_tokens_details=None,
        ),
    )


class _FakeCompletions:
    def __init__(self, chunks, raise_after=None):
        self._chunks = chunks
        self._raise_after = raise_after
        self.last_kwargs: dict = {}

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        chunks = self._chunks
        raise_after = self._raise_after

        def _iter():
            for i, chunk in enumerate(chunks):
                if raise_after is not None and i == raise_after:
                    raise RuntimeError("upstream exploded")
                yield chunk

        return _iter()


@pytest.fixture
def stream_env(monkeypatch):
    """openai 経路の fake client + UsageEvent の捕捉。"""
    settings = SimpleNamespace(llm_provider="openai")
    monkeypatch.setattr(llm_mod, "get_settings", lambda: settings)

    events: list = []
    monkeypatch.setattr(observe_mod, "record", lambda event: events.append(event))

    state = SimpleNamespace(completions=None, events=events)

    def _install(chunks, raise_after=None):
        completions = _FakeCompletions(chunks, raise_after=raise_after)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        monkeypatch.setattr(llm_mod, "_get_openai_client", lambda: client)
        state.completions = completions
        return completions

    state.install = _install
    return state


MESSAGES = [{"role": "user", "content": "テストの発話"}]


# ===========================================================================
# 1. delta の中身（ST6: delta には本文以外を載せない）
# ===========================================================================


class TestDeltas:
    def test_yields_only_content_pieces(self, stream_env):
        stream_env.install([_delta_chunk("あい"), _delta_chunk("うえ"), _usage_chunk()])

        pieces = list(llm_mod.generate_text_stream(MESSAGES, model="m-1"))

        assert pieces == ["あい", "うえ"]

    def test_empty_and_none_deltas_are_skipped(self, stream_env):
        stream_env.install([
            _delta_chunk("a"),
            _delta_chunk(None),
            _delta_chunk(""),
            _delta_chunk("b"),
        ])

        assert list(llm_mod.generate_text_stream(MESSAGES, model="m-1")) == ["a", "b"]

    def test_stream_flags_are_passed_to_the_api(self, stream_env):
        completions = stream_env.install([_delta_chunk("x")])

        list(llm_mod.generate_text_stream(MESSAGES, model="m-1", temperature=0.3))

        assert completions.last_kwargs["stream"] is True
        assert completions.last_kwargs["stream_options"] == {"include_usage": True}
        assert completions.last_kwargs["model"] == "m-1"


# ===========================================================================
# 2. 観測（ST3 / U1 / §6）
# ===========================================================================


class TestObservation:
    def test_reported_usage_from_the_final_chunk(self, stream_env):
        stream_env.install([_delta_chunk("ab"), _usage_chunk(prompt=11, completion=7)])

        list(llm_mod.generate_text_stream(MESSAGES, model="m-1"))

        assert len(stream_env.events) == 1
        event = stream_env.events[0]
        assert event.usage_source == "reported"
        assert event.prompt_tokens == 11
        assert event.completion_tokens == 7
        assert event.operation == "chat"
        assert event.metadata.get("streamed") is True
        assert event.success is True

    def test_estimated_when_no_usage_chunk(self, stream_env):
        stream_env.install([_delta_chunk("ab"), _delta_chunk("cd")])

        list(llm_mod.generate_text_stream(MESSAGES, model="m-1"))

        assert len(stream_env.events) == 1
        event = stream_env.events[0]
        assert event.usage_source.startswith("estimated")
        assert event.metadata.get("streamed") is True
        assert event.output_characters == 4

    def test_observed_exactly_once_on_abort(self, stream_env):
        stream_env.install([_delta_chunk("ab"), _delta_chunk("cd"), _usage_chunk()])

        gen = llm_mod.generate_text_stream(MESSAGES, model="m-1")
        assert next(gen) == "ab"
        gen.close()

        assert len(stream_env.events) == 1
        event = stream_env.events[0]
        assert event.metadata.get("streamed") is True
        assert event.metadata.get("client_aborted") is True
        # 中断時は実測 usage が来ないので推計として正直に記録する（U1）。
        assert event.usage_source.startswith("estimated")

    def test_observed_exactly_once_on_error(self, stream_env):
        stream_env.install([_delta_chunk("ab"), _delta_chunk("cd")], raise_after=1)

        gen = llm_mod.generate_text_stream(MESSAGES, model="m-1")
        with pytest.raises(RuntimeError):
            list(gen)

        assert len(stream_env.events) == 1
        event = stream_env.events[0]
        assert event.success is False
        assert event.error_type == "RuntimeError"
        assert event.metadata.get("streamed") is True
        assert event.metadata.get("client_aborted") is None

    def test_usage_ctx_is_passed_by_value(self, stream_env):
        stream_env.install([_delta_chunk("ab"), _usage_chunk()])

        list(llm_mod.generate_text_stream(
            MESSAGES,
            model="m-1",
            usage_ctx={
                "feature": "learning:chat_discuss",
                "user_id": "user-1",
                "course_id": "course-1",
            },
        ))

        event = stream_env.events[0]
        assert event.feature == "learning:chat_discuss"
        assert event.user_id == "user-1"
        assert event.course_id == "course-1"

    def test_without_usage_ctx_falls_back_to_the_ambient_context(self, stream_env):
        from core.llm_usage.context import usage_context

        stream_env.install([_delta_chunk("ab"), _usage_chunk()])

        with usage_context("learning:chat", user_id="user-2"):
            list(llm_mod.generate_text_stream(MESSAGES, model="m-1"))

        assert stream_env.events[0].feature == "learning:chat"


# ===========================================================================
# 3. 非 openai プロバイダのフォールバック（原則8: ストリームのふりをしない）
# ===========================================================================


class TestNonOpenAiProviders:
    @pytest.mark.parametrize("provider", ["gemini", "google", "gemini-vertex"])
    def test_single_delta_fallback(self, monkeypatch, provider):
        monkeypatch.setattr(
            llm_mod, "get_settings", lambda: SimpleNamespace(llm_provider=provider)
        )
        events: list = []
        monkeypatch.setattr(observe_mod, "record", lambda event: events.append(event))
        monkeypatch.setattr(llm_mod, "generate_text", lambda *a, **k: "まとめて返る本文")

        pieces = list(llm_mod.generate_text_stream(MESSAGES, model="m-1"))

        assert pieces == ["まとめて返る本文"]
        # 観測は generate_text 側の責務（ここで二重記録しない）。
        assert events == []

    def test_fallback_reapplies_usage_ctx_for_attribution(self, monkeypatch):
        """§3.2: フォールバック経路は threadpool の別 context で走るので、
        generate_text 側の観測に U層帰属が付くよう usage_ctx を再セットする。"""
        from core.llm_usage.context import current_usage_context

        monkeypatch.setattr(
            llm_mod, "get_settings", lambda: SimpleNamespace(llm_provider="gemini")
        )
        seen: list[str] = []

        def _fake_generate_text(*a, **k):
            seen.append(current_usage_context().feature)
            return "本文"

        monkeypatch.setattr(llm_mod, "generate_text", _fake_generate_text)
        pieces = list(
            llm_mod.generate_text_stream(
                MESSAGES, model="m-1", usage_ctx={"feature": "learning:chat"}
            )
        )
        assert pieces == ["本文"]
        assert seen == ["learning:chat"]
        # with を抜けてから yield しているので、呼び出し側の context は汚れない
        assert current_usage_context().feature != "learning:chat"
