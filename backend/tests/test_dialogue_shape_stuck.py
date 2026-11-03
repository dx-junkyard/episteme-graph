"""応答の骨格: 「か。」で終わる問い返し と 詰まりの区画（第 17 周）。

正本: docs/features/dialogue_response_shape_design.md §8。
"""

from __future__ import annotations

from tests.test_ik0383_prerequisite_followup import (  # noqa: F401  (sys.path を整える)
    _chat,
    chat_env,
)

import routes.learning as learning_mod  # noqa: E402
from core.dialogue_shape import (  # noqa: E402
    assemble,
    is_stuck_reply,
    render_answer_text,
    split_closing_question,
)


class TestKaQuestion:
    def test_ka_ending_questions_are_trailing_questions(self):
        for q in ("どれが一番わかりにくかったでしょうか。", "試してみますか。", "どちらですか。", "これでいいかな。"):
            body, qs = split_closing_question("説明です。" + q)
            assert qs == [q] and body == "説明です。"

    def test_mixed_question_chain(self):
        _body, qs = split_closing_question("説明。どちらが正しいのか？ 試してみますか。")
        assert qs == ["どちらが正しいのか？", "試してみますか。"]

    def test_non_question_ka_endings_are_kept(self):
        for t in ("それは確か。", "これがどれほど重要なことか。", "問題は収束するかどうか。", "違いは明らか。"):
            assert split_closing_question("前文。" + t)[1] == []

    def test_long_ka_sentence_is_not_a_question(self):
        long = "あ" * 90 + "ではないか。"
        assert split_closing_question("前文。" + long)[1] == []

    def test_ka_question_is_dropped_on_gate_turn(self):
        shape = assemble(
            answer="説明本文。どれがわかりにくかったでしょうか。", learner_message="x",
            is_discuss=False, gate_text="前提Xは大丈夫ですか？", degraded=False,
        )
        assert "でしょうか" not in render_answer_text(shape).split("\n\n---\n\n")[0]

    def test_byte_identity_when_nothing_dropped(self):
        a = "説明本文。\n\n試してみますか。"
        shape = assemble(answer=a, learner_message="x", is_discuss=False, gate_text=None, degraded=False)
        assert render_answer_text(shape) == a
        assert shape.closing_question == "試してみますか。"


class TestStuckReply:
    def test_stuck_vocabulary(self):
        for m in ("わからない", "まだわからない", "むずい。", "わからん…", "難しいです", "IDK", "全然分からない"):
            assert is_stuck_reply(m), m

    def test_not_stuck(self):
        for m in ("はい", "なるほど", "むずい。一言で言うと？", "どこから読めばいい？", "", None):
            assert not is_stuck_reply(m), m

    def test_stuck_keeps_one_foothold_without_question(self):
        a = "Aです。Bです。Cです。Dです。\n\n## 2\n\n長い説明。\n\nどれが一番わかりにくかったでしょうか。\n\n[ACTION_BUTTON: Aについて聞く]"
        shape = assemble(answer=a, learner_message="わからない", is_discuss=False,
                         gate_text=None, degraded=False, stuck=True)
        assert render_answer_text(shape) == "Aです。Bです。Cです。\n\n[ACTION_BUTTON: Aについて聞く]"
        assert shape.closing_question is None

    def test_stuck_short_answer_is_byte_identical(self):
        a = "まず、Aだけ見ましょう。"
        shape = assemble(answer=a, learner_message="わからない", is_discuss=False,
                         gate_text=None, degraded=False, stuck=True)
        assert render_answer_text(shape) == a

    def test_stuck_degraded_passthrough(self):
        shape = assemble(answer="固定文。", learner_message="わからない", is_discuss=False,
                         gate_text=None, degraded=True, stuck=True)
        assert render_answer_text(shape) == "固定文。"


def _answer_with(monkeypatch, chat_env, text):
    def _gen(**kwargs):
        chat_env.prompts.append(" ".join(str(m.get("content")) for m in kwargs.get("messages") or []))
        return text

    monkeypatch.setattr(learning_mod, "generate_text", _gen)


def test_stuck_turn_defers_gate_and_does_not_record_it(chat_env, monkeypatch):
    _answer_with(monkeypatch, chat_env, "足場1。足場2。足場3。足場4。\n\n別の節。\n\nどこがわかりにくかったですか。")
    resp = _chat("わからない")
    assert "\n\n---\n\n" not in resp.answer
    assert resp.answer.endswith("足場1。足場2。足場3。") and "足場4" not in resp.answer
    assert resp.shape == {"closing_question": None, "has_gate": False, "mirror_kept": False}
    # 逆質問は出していないので「提示した」記帳もしない（次の往復で 1 回だけ出る）。
    assert not chat_env.presented
    resp2 = _chat("µ0≈1.04 の出所は？")
    assert "\n\n---\n\n" in resp2.answer
    assert chat_env.presented


def test_tutor_prompt_has_stuck_rule():
    prompt = learning_mod._get_integrated_tutor_system_prompt("")
    assert "【詰まり】" in prompt and "問い返しはしないでください" in prompt
