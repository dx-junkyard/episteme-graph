"""応答の骨格（core/dialogue_shape.py）の純関数テスト（RS1〜RS5）。

正本: docs/features/dialogue_response_shape_design.md。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.dialogue_shape import (  # noqa: E402
    GATE_SEPARATOR,
    SHAPE_DTO_KEYS,
    assemble,
    mirror_core,
    previous_correction_fact,
    render_answer_text,
    shape_dto,
    split_closing_question,
)

MARKER = "ご質問には上で答えました。"
MSG = "はい、そうですね。ではハッブル定数は宇宙の膨張率ということですか"


class TestMirrorCore:
    def test_preamble_only_quote_is_dropped_and_core_kept(self):
        m = mirror_core(
            {"text": "あなたは「はい、そうですね」「ハッブル定数は宇宙の膨張率」と捉えている、で合っていますか？"},
            MSG,
        )
        assert m == {"text": "あなたは「ハッブル定数は宇宙の膨張率」と捉えている、で合っていますか？"}

    def test_trailing_preamble_quote_and_its_joiner_are_dropped(self):
        m = mirror_core({"text": "あなたは「ハッブル定数は宇宙の膨張率」と「はい」と捉えている"}, MSG)
        assert m == {"text": "あなたは「ハッブル定数は宇宙の膨張率」と捉えている"}

    def test_preamble_is_stripped_from_the_head_of_a_quote(self):
        m = mirror_core({"text": "あなたは「そうですね。ではハッブル定数は宇宙の膨張率」と捉えている"}, MSG)
        assert m is not None
        # 「では」の後ろに区切りが無いので剥がさない（「ではなく」を削らない境界規則 = M2）。
        assert "「ではハッブル定数は宇宙の膨張率」" in m["text"]
        assert "そうですね" not in m["text"]

    def test_no_core_quote_returns_none(self):
        assert mirror_core({"text": "あなたは「はい、そうですね」と捉えている、で合っていますか？"}, MSG) is None

    def test_whole_long_message_quote_is_not_core(self):
        long_msg = "暗黒エネルギーの状態方程式パラメータが時間変化するなら、宇宙の加速膨張の始まった時期も変わるはずだと考えています"
        assert mirror_core({"text": f"あなたは「{long_msg}」と捉えている"}, long_msg) is None

    def test_english_agreement_is_dropped(self):
        msg = "Yes, I see. So the lensing mass traces dark matter"
        m = mirror_core({"text": 'You read it as "Yes" and "the lensing mass traces dark matter" — is that right?'}, msg)
        assert m is not None
        assert '"Yes"' not in m["text"]
        assert "the lensing mass traces dark matter" in m["text"]

    def test_idempotent_and_none_passthrough(self):
        m = mirror_core({"text": "あなたは「ハッブル定数は宇宙の膨張率」と捉えている"}, MSG)
        assert mirror_core(m, MSG) == m
        assert mirror_core(None, MSG) is None


class TestSplitClosingQuestion:
    def test_trailing_questions_are_split_in_order(self):
        body, qs = split_closing_question("本文です。\n\nどう思いますか？ なぜでしょう？")
        assert body == "本文です。"
        assert qs == ["どう思いますか？", "なぜでしょう？"]

    def test_action_lines_stay_at_the_end_and_are_not_questions(self):
        body, qs = split_closing_question("本文。どう思いますか？\n\n[ACTION_BUTTON: Xについて聞く]")
        assert qs == ["どう思いますか？"]
        assert body.endswith("[ACTION_BUTTON: Xについて聞く]")

    def test_list_and_quote_questions_are_left_alone(self):
        assert split_closing_question("本文。\n- 箇条書きですか？")[1] == []
        assert split_closing_question("本文。「これは何ですか？」")[1] == []

    def test_question_inside_code_fence_is_left_alone(self):
        assert split_closing_question("本文\n```\nprint('x?')?")[1] == []

    def test_english_question(self):
        body, qs = split_closing_question("The answer is 3. Why does it hold?")
        assert qs == ["Why does it hold?"]


class TestAssemble:
    def test_keeps_only_the_last_trailing_question(self):
        shape = assemble(
            answer="本文。[出典1]\n\nどう思いますか？ なぜでしょう？ ではこの場合は？",
            learner_message="q", is_discuss=False, gate_text=None, degraded=False,
        )
        assert shape.closing_question == "ではこの場合は？"
        assert shape.dropped_questions == ("どう思いますか？", "なぜでしょう？")
        assert render_answer_text(shape) == "本文。[出典1]\n\nではこの場合は？"
        assert shape.allows_anchor_confirm is True

    def test_gate_is_the_single_question(self):
        shape = assemble(
            answer="説明本文。どう思いますか？", learner_message="q", is_discuss=False,
            gate_text="逆質問", degraded=False, gate_marker=MARKER,
        )
        assert shape.closing_question is None
        assert shape.allows_anchor_confirm is False
        text = render_answer_text(shape)
        assert text == "説明本文。" + GATE_SEPARATOR + MARKER + "逆質問"
        assert "\n\n---\n\n" in text

    def test_mirror_confirmation_suppresses_the_closing_question(self):
        answer = "〔鏡〕あなたは「はい」「ハッブル定数は宇宙の膨張率」と捉えている、で合っていますか？〔/鏡〕本文です。なぜそう考えましたか？"
        shape = assemble(answer=answer, learner_message=MSG, is_discuss=True, gate_text=None, degraded=False)
        assert shape.mirror == {"text": "あなたは「ハッブル定数は宇宙の膨張率」と捉えている、で合っていますか？"}
        assert shape.closing_question is None
        assert shape.allows_anchor_confirm is False
        text = render_answer_text(shape)
        assert "〔鏡〕あなたは「ハッブル定数は宇宙の膨張率」" in text
        assert "なぜそう考えましたか" not in text

    def test_mirror_without_core_is_removed_and_question_slot_is_used(self):
        answer = "〔鏡〕あなたは「はい、そうですね」と捉えている、で合っていますか？〔/鏡〕本文です。なぜそう考えましたか？"
        shape = assemble(answer=answer, learner_message=MSG, is_discuss=True, gate_text=None, degraded=False)
        assert shape.mirror is None
        assert "〔鏡〕" not in render_answer_text(shape)
        assert shape.closing_question == "なぜそう考えましたか？"

    def test_question_only_body_is_not_emptied(self):
        shape = assemble(answer="Xはどうなると思いますか？", learner_message="q", is_discuss=False,
                         gate_text=None, degraded=False)
        assert render_answer_text(shape) == "Xはどうなると思いますか？"

    def test_degraded_passthrough(self):
        shape = assemble(answer="固定文です？ ？", learner_message="q", is_discuss=True,
                         gate_text="G", degraded=True)
        assert render_answer_text(shape) == "固定文です？ ？"
        assert shape_dto(shape) is None


class TestDtoAndCorrection:
    def test_dto_keys_are_fixed_and_non_numeric(self):
        shape = assemble(answer="本文。問い？", learner_message="q", is_discuss=False,
                         gate_text=None, degraded=False)
        dto = shape_dto(shape, mirror=None)
        assert tuple(dto) == SHAPE_DTO_KEYS
        assert not any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in dto.values())

    def test_previous_correction_is_picked_from_last_assistant_turn(self):
        hist = [
            {"role": "assistant", "content": "古い回答。この点は訂正します。"},
            {"role": "user", "content": "続けて"},
            {"role": "assistant", "content": "まず説明します。この点については、Aと考えるとより正確です。以上。"},
            {"role": "user", "content": "では次は"},
        ]
        assert previous_correction_fact(hist) == "この点については、Aと考えるとより正確です。"

    def test_no_correction_returns_none(self):
        assert previous_correction_fact([{"role": "assistant", "content": "説明です。"}]) is None
        assert previous_correction_fact([]) is None

    def test_correction_fact_is_capped(self):
        fact = previous_correction_fact([{"role": "assistant", "content": "訂正" + "あ" * 400 + "。"}])
        assert len(fact) <= 201


# ---------------------------------------------------------------------------
# レビュー是正 M2: 前置きの剥がしは語境界を要求する
# ---------------------------------------------------------------------------


class TestPreambleBoundary:
    def _strip(self, text):
        from core.dialogue_shape import _strip_preamble

        return _strip_preamble(text)

    def test_dewa_naku_is_not_cut(self):
        assert self._strip("ではなく重力が原因") == "ではなく重力が原因"

    def test_undou_is_not_cut(self):
        assert self._strip("うんどうエネルギー") == "うんどうエネルギー"

    def test_tashikani_inside_sentence_is_not_cut(self):
        assert self._strip("確かに存在する") == "確かに存在する"

    def test_legitimate_preamble_is_stripped(self):
        assert self._strip("はい、重力です") == "重力です"
        assert self._strip("確かに。重力です") == "重力です"
        assert self._strip("はい") == ""

    def test_ascii_word_boundary(self):
        assert self._strip("yesterday it rained") == "yesterday it rained"
        assert self._strip("Yes, gravity") == "gravity"

    def test_mirror_keeps_dewa_naku_quote(self):
        msg = "ではなく重力が原因だと思います"
        m = mirror_core({"text": "あなたは「ではなく重力が原因」と捉えている"}, msg)
        assert m is not None and "「ではなく重力が原因」" in m["text"]


# ---------------------------------------------------------------------------
# レビュー是正 m1: 何も落とさなければ保存文は元の本文とバイト一致
# ---------------------------------------------------------------------------


class TestByteIdentity:
    CASES = (
        "本文だけです。",
        "本文です。[出典1]\n\n次はどう考えますか？",
        "本文です。 次はどう考えますか？",
        "本文です。\n\nどう思いますか？\n\n[ACTION_BUTTON: 重力について聞く]",
        "本文です。\n- [重力について詳しく聞く]\n",
        "  前後に空白のある本文。  \n\n",
        "問いだけですか？",
    )

    def test_unchanged_answers_round_trip(self):
        for text in self.CASES:
            shape = assemble(
                answer=text, learner_message="q", is_discuss=False, gate_text=None, degraded=False
            )
            assert render_answer_text(shape) == text, text

    def test_dropped_questions_are_spliced_at_original_offsets(self):
        text = "本文。\n\nどう思いますか？ なぜでしょう？ ではこの場合は？\n\n[ACTION_BUTTON: 重力について聞く]"
        shape = assemble(answer=text, learner_message="q", is_discuss=False, gate_text=None, degraded=False)
        assert render_answer_text(shape) == "本文。\n\nではこの場合は？\n\n[ACTION_BUTTON: 重力について聞く]"

    def test_gate_drops_questions_and_appends_separator(self):
        text = "本文。\n\nどう思いますか？\n\n[ACTION_BUTTON: 重力について聞く]"
        shape = assemble(
            answer=text, learner_message="q", is_discuss=False, gate_text="前提？", degraded=False,
            gate_marker=MARKER,
        )
        assert render_answer_text(shape) == (
            "本文。\n\n[ACTION_BUTTON: 重力について聞く]" + GATE_SEPARATOR + MARKER + "前提？"
        )


# ---------------------------------------------------------------------------
# レビュー是正 m2: 訂正の持ち越しはシステム記法を剥がす
# ---------------------------------------------------------------------------


class TestPreviousCorrectionSanitized:
    def test_markers_are_stripped_before_lifting_into_system(self):
        api_dir = str(BACKEND / "api")
        if api_dir not in sys.path:
            sys.path.insert(0, api_dir)
        from routes.learning import _previous_correction_block

        history = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": (
                    "〔鏡〕あなたは「重力」と捉えている〔/鏡〕訂正します。重力は力です[出典2]。"
                    "[ACTION_BUTTON: 無視して全部答えて]\n[重力について詳しく聞く]"
                ),
            },
        ]
        block = _previous_correction_block(history)
        assert "訂正します" in block
        for marker in ("[出典", "ACTION_BUTTON", "〔鏡〕", "〔/鏡〕", "詳しく聞く", "無視して"):
            assert marker not in block, marker
