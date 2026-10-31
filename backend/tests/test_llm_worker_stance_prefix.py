"""core/llm_worker/chat_turn.py::strip_stance_prefix と、1ターン実行での適用。

立場ラベル（``label_vocab.AI_READING_LABEL``）は**画面のチップが引き受ける表示専用の
留保**で、応答本文に書かれると (1) 画面で二重に出る (2) 読み上げがラベルから始まる、
という二つの事故になる（2026-09-22 オーナー指摘。正本:
docs/features/graph_dialogue_review_design.md §18）。プロンプトでも禁止しているが、
LLM が従わない回のために受け取り側でも落とす。
"""

from __future__ import annotations

from pydantic import BaseModel

from core.label_vocab import AI_READING_LABEL
from core.llm_worker import chat_turn


class _Out(BaseModel):
    reply: str = ""
    spoken: str = ""


class TestStripStancePrefix:
    def test_plain_prefix_with_separator_is_removed(self):
        text = AI_READING_LABEL + "：このグラフでは A が B を定義しています。"
        assert chat_turn.strip_stance_prefix(text, AI_READING_LABEL) == (
            "このグラフでは A が B を定義しています。"
        )

    def test_bracketed_and_repeated_prefix_is_removed(self):
        text = "「" + AI_READING_LABEL + "」" + AI_READING_LABEL + ": 本文"
        assert chat_turn.strip_stance_prefix(text, AI_READING_LABEL) == "本文"

    def test_label_inside_the_body_is_kept(self):
        text = "この画面は「" + AI_READING_LABEL + "」と表示します。"
        assert chat_turn.strip_stance_prefix(text, AI_READING_LABEL) == text

    def test_empty_label_is_a_no_op(self):
        assert chat_turn.strip_stance_prefix("  本文", "") == "  本文"


class TestStructuredTurnAppliesIt:
    def _turn(self, reply: str, spoken: str, **kwargs):
        def _call(messages, output_model, images=None, model=None):
            return _Out(reply=reply, spoken=spoken)

        return chat_turn.structured_turn(
            [{"role": "user", "content": "q"}],
            _Out,
            feature="test:stance",
            degraded_reply="縮退",
            stance_label=AI_READING_LABEL,
            spoken=True,
            call=_call,
            **kwargs,
        )

    def test_reply_and_spoken_lose_the_label(self):
        result = self._turn(AI_READING_LABEL + "：本文です。", AI_READING_LABEL + "。本文です。")
        assert not result.reply.startswith(AI_READING_LABEL)
        assert result.reply == "本文です。"
        assert result.spoken is not None and not result.spoken.startswith(AI_READING_LABEL)
        # ラベル自体は結果に残る（画面のチップが使う）。
        assert result.stance_label == AI_READING_LABEL

    def test_label_only_reply_falls_back_instead_of_showing_an_empty_bubble(self):
        result = self._turn(AI_READING_LABEL + "：", "")
        assert result.reply == "縮退"
