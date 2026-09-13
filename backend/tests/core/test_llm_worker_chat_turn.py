"""``core/llm_worker/chat_turn.py`` — 同期1ターン会話の共通骨格。

W層の要素対話 / グラフ全体対話 / 教材図スタジオが共有する

- メッセージ組み立て（grounding の注入位置・読み上げ契約の opt-in）
- 読み上げモードのスキーマ生成（``spoken_variant``）
- ``usage_context`` の内側での1コール + 縮退（``structured_turn``）

を、**移行前の逐語実装と出力が一致すること**を軸に固定する。
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from core.llm_usage.context import current_usage_context
from core.llm_worker.chat_turn import (
    MATH_DELIMITER_INSTRUCTION,
    SPOKEN_CONTRACT,
    TurnResult,
    build_turn_messages,
    spoken_variant,
    structured_turn,
)


# ---------------------------------------------------------------------------
# 移行前の逐語実装（dialogue.py / graph_dialogue.py に同一のものが2本あった）
# ---------------------------------------------------------------------------


def _legacy_build_llm_messages(prior_messages, user_content, grounding_text, header, spoken_contract, response_mode="text"):
    header_text = header
    if response_mode == "spoken":
        header_text = header + spoken_contract
    turns = list(prior_messages) + [{"role": "user", "content": user_content}]
    messages: list[dict[str, str]] = []
    first_user_injected = False
    for turn in turns:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        if not first_user_injected and role == "user":
            first_user_injected = True
            if grounding_text:
                content = header_text + "\n\n" + grounding_text + "\n\n---\n\n" + content
        messages.append({"role": role, "content": content})
    return messages


_HISTORIES = [
    [],
    [{"role": "user", "content": "1st"}],
    [{"role": "assistant", "content": "先に助手"}, {"role": "user", "content": "1st"}],
    [
        {"role": "user", "content": "1st"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": "2nd"},
    ],
    [{"role": "assistant", "content": "助手だけ"}],
    [{"content": "role なし"}],  # role 既定は user
    [{"role": "user"}],  # content 既定は空文字
]


class TestBuildTurnMessagesParity:
    @pytest.mark.parametrize("prior", _HISTORIES)
    @pytest.mark.parametrize("grounding", ["GROUNDING", ""])
    @pytest.mark.parametrize("mode", ["text", "spoken"])
    def test_matches_the_legacy_implementation(self, prior, grounding, mode):
        expected = _legacy_build_llm_messages(
            prior, "新しい発話", grounding, "HEADER", SPOKEN_CONTRACT, response_mode=mode,
        )
        got = build_turn_messages(
            prior,
            "新しい発話",
            header="HEADER",
            grounding_text=grounding,
            spoken=mode == "spoken",
        )
        assert got == expected

    def test_does_not_mutate_the_caller_history(self):
        prior = [{"role": "user", "content": "1st"}]
        build_turn_messages(
            prior, "2nd", header="H", grounding_text="G",
        )
        assert prior == [{"role": "user", "content": "1st"}]

    def test_inject_current_user_targets_the_new_turn(self):
        messages = build_turn_messages(
            [{"role": "user", "content": "1st"}],
            "いまの発話",
            header="H",
            grounding_text="G",
            inject="current_user",
        )
        assert "G" not in messages[0]["content"]
        assert messages[1]["content"].startswith("H\n\nG\n\n---\n\n")
        assert messages[1]["content"].endswith("いまの発話")

    def test_inject_none_leaves_every_turn_untouched(self):
        messages = build_turn_messages(
            [{"role": "user", "content": "1st"}],
            "2nd",
            header="H",
            grounding_text="G",
            inject="none",
        )
        assert [m["content"] for m in messages] == ["1st", "2nd"]

    def test_none_history_is_accepted(self):
        assert build_turn_messages(None, "q", header="H", grounding_text="") == [
            {"role": "user", "content": "q"}
        ]

    def test_math_delimiter_contract_text(self):
        # 表記契約の正本（各系統はこれを連結する）。
        assert "数式は必ず `$…$` で区切ってください" in MATH_DELIMITER_INSTRUCTION
        assert r"\(" in MATH_DELIMITER_INSTRUCTION


# ---------------------------------------------------------------------------
# spoken_variant: 手書きサブクラスとスキーマが一致すること
# ---------------------------------------------------------------------------


class _Annotation(BaseModel):
    kind: str = ""
    evidence: list[str] = Field(default_factory=list)


class _Base(BaseModel):
    reply: str = ""
    annotations: list[_Annotation] = Field(default_factory=list)


class _BaseSpoken(_Base):
    """読み上げモードの structured output（``spoken`` を同一コールで受け取る）。

    text 経路のスキーマは一切変えない。
    """

    spoken: str = ""


class TestSpokenVariant:
    def test_schema_matches_the_handwritten_subclass(self):
        generated = spoken_variant(_Base, name="_BaseSpoken", doc=_BaseSpoken.__doc__)
        assert generated.model_json_schema() == _BaseSpoken.model_json_schema()

    def test_default_name_and_field(self):
        generated = spoken_variant(_Base)
        assert generated.__name__ == "_BaseSpoken"
        assert generated().spoken == ""
        assert issubclass(generated, _Base)

    def test_variant_reports_the_owning_module(self):
        assert spoken_variant(_Base).__module__ == _Base.__module__

    def test_text_schema_is_untouched(self):
        before = _Base.model_json_schema()
        spoken_variant(_Base)
        assert _Base.model_json_schema() == before
        assert "spoken" not in before["properties"]

    def test_live_dialogue_schemas_match_their_handwritten_originals(self):
        """本番の2系統が移行前と同一の JSON schema を送ること（title/description 込み）。"""
        from core.deliberation import dialogue, graph_dialogue

        class _DialogueTurnOutputSpoken(dialogue._DialogueTurnOutput):  # noqa: SLF001
            """読み上げモードの structured output（``spoken`` を同一コールで受け取る）。

            text 経路のスキーマ（``_DialogueTurnOutput``）は**一切変えない** — 既存の
            プロンプト・スキーマをバイト単位で維持するため、別クラスに分ける。
            """

            spoken: str = ""

        class _GraphTurnOutputSpoken(graph_dialogue._GraphTurnOutput):  # noqa: SLF001
            """読み上げモードの structured output（``spoken`` を**同じ1コール**で受け取る）。

            text 経路のスキーマ（``_GraphTurnOutput``）は変えない — 既存プロンプト・
            スキーマをバイト単位で維持するため別クラスにする（§15）。
            """

            spoken: str = ""

        assert (
            dialogue._DialogueTurnOutputSpoken.model_json_schema()  # noqa: SLF001
            == _DialogueTurnOutputSpoken.model_json_schema()
        )
        assert (
            graph_dialogue._GraphTurnOutputSpoken.model_json_schema()  # noqa: SLF001
            == _GraphTurnOutputSpoken.model_json_schema()
        )


# ---------------------------------------------------------------------------
# structured_turn
# ---------------------------------------------------------------------------


class _Out(BaseModel):
    reply: str = ""
    spoken: str = ""


def _messages():
    return [{"role": "user", "content": "q"}]


class TestStructuredTurn:
    def test_happy_path_returns_parsed_and_hygiened_reply(self):
        seen: dict = {}

        def _call(messages, output_model, *, images=None, model=None):
            seen.update(messages=messages, output_model=output_model, images=images, model=model)
            return _Out(reply="\x1b[0m本文[0m  ")

        result = structured_turn(
            _messages(), _Out, feature="test:chat", degraded_reply="DEGRADED",
            model="m1", call=_call,
        )
        assert isinstance(result, TurnResult)
        assert result.reply == "本文"
        assert result.raw == "\x1b[0m本文[0m  "
        assert result.degraded is False
        assert result.parsed.reply.endswith("  ")
        assert seen["model"] == "m1"
        assert seen["output_model"] is _Out

    def test_exception_degrades_without_raising(self):
        def _boom(*args, **kwargs):
            raise RuntimeError("llm down")

        result = structured_turn(
            _messages(), _Out, feature="test:chat", degraded_reply="DEGRADED", call=_boom,
        )
        assert result.degraded is True
        assert result.reply == "DEGRADED"
        assert result.parsed is None
        assert result.spoken is None

    def test_degraded_spoken_uses_the_resolver_on_the_degraded_text(self):
        def _boom(*args, **kwargs):
            raise RuntimeError("llm down")

        seen: list = []

        def _resolver(spoken, reply):
            seen.append((spoken, reply))
            return f"[spoken]{reply}"

        result = structured_turn(
            _messages(), _Out, feature="test:chat", degraded_reply="DEGRADED",
            call=_boom, spoken=True, spoken_resolver=_resolver,
        )
        assert result.spoken == "[spoken]DEGRADED"
        assert seen == [("", "DEGRADED")]

    def test_spoken_resolver_receives_the_llm_spoken_and_final_reply(self):
        result = structured_turn(
            _messages(), _Out, feature="test:chat", degraded_reply="DEGRADED",
            call=lambda *a, **k: _Out(reply="本文", spoken="よみあげ"),
            spoken=True, spoken_resolver=lambda spoken, reply: f"{spoken}/{reply}",
        )
        assert result.spoken == "よみあげ/本文"

    def test_empty_reply_falls_back_to_the_degraded_text_without_the_flag(self):
        result = structured_turn(
            _messages(), _Out, feature="test:chat", degraded_reply="DEGRADED",
            call=lambda *a, **k: _Out(reply="   "),
        )
        assert result.reply == "DEGRADED"
        assert result.degraded is False

    def test_empty_reply_fallback_can_be_disabled(self):
        result = structured_turn(
            _messages(), _Out, feature="test:chat", degraded_reply="DEGRADED",
            call=lambda *a, **k: _Out(reply="   "), empty_reply_fallback=None,
        )
        assert result.reply == ""

    def test_hygiene_can_be_disabled(self):
        result = structured_turn(
            _messages(), _Out, feature="test:chat", degraded_reply="DEGRADED",
            call=lambda *a, **k: _Out(reply="\x1b[0m本文"), hygiene=None,
        )
        assert result.reply == "\x1b[0m本文"

    def test_stance_label_is_passed_through(self):
        result = structured_turn(
            _messages(), _Out, feature="test:chat", degraded_reply="DEGRADED",
            call=lambda *a, **k: _Out(reply="ok"), stance_label="LABEL",
        )
        assert result.stance_label == "LABEL"

    def test_callable_model_is_resolved_inside_the_usage_context(self):
        """M層のユーザー別ポリシーが user_id を見られること（外側で解決すると常に None）。"""
        seen: dict = {}

        def _resolve():
            ctx = current_usage_context()
            seen["feature"] = ctx.feature
            seen["user_id"] = ctx.user_id
            seen["document_id"] = ctx.document_id
            return "resolved-model"

        def _call(messages, output_model, *, images=None, model=None):
            seen["model"] = model
            seen["call_user_id"] = current_usage_context().user_id
            return _Out(reply="ok")

        structured_turn(
            _messages(), _Out, feature="test:vision", degraded_reply="D",
            model=_resolve, user_id="teacher-1", document_id="doc-1", call=_call,
        )
        assert seen["feature"] == "test:vision"
        assert seen["user_id"] == "teacher-1"
        assert seen["document_id"] == "doc-1"
        assert seen["model"] == "resolved-model"
        assert seen["call_user_id"] == "teacher-1"

    def test_usage_context_is_left_after_the_turn(self):
        before = current_usage_context()
        structured_turn(
            _messages(), _Out, feature="test:chat", degraded_reply="D",
            call=lambda *a, **k: _Out(reply="ok"),
        )
        assert current_usage_context() == before

    def test_course_id_attribution_is_forwarded(self):
        seen: dict = {}

        def _call(*args, **kwargs):
            seen["course_id"] = current_usage_context().course_id
            return _Out(reply="ok")

        structured_turn(
            _messages(), _Out, feature="test:chat", degraded_reply="D",
            course_id="course-1", call=_call,
        )
        assert seen["course_id"] == "course-1"


class TestCoreIsolation:
    def test_module_does_not_import_fastapi_or_domain_env_names(self):
        from pathlib import Path

        from tests.guardrail_helpers import assert_source_does_not_import

        src = (
            Path(__file__).resolve().parents[2] / "core" / "llm_worker" / "chat_turn.py"
        ).read_text(encoding="utf-8")
        assert_source_does_not_import(
            src, ["fastapi", "routes", "services"], context="core/llm_worker/chat_turn.py",
        )
        # ドメイン固有の設定キー・feature 文字列を共通骨格に置かない。
        for forbidden in ("_MAX_CALLS", "llm_model", "deliberation:", "admin:"):
            assert forbidden not in src, forbidden
