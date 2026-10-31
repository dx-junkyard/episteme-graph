"""IK-0574: 前提ゲートの逆質問を学習者の当該発話の言語で出す（英語なら英語）。

正本: docs/features/dialogue_response_shape_design.md §8.2 / docs/issue_knowledge/entries/IK-0574-*.md。
判定器は新設せず既存の ``_is_mostly_latin``（IK-0424）/ ``_is_kana_kanji_free``（IK-0450）の和。
保存文の形（本文 → ``\\n\\n---\\n\\n`` + 目印 + 逆質問）は不変で、履歴の判定器は英語の逆質問も読む。
"""

from __future__ import annotations

import re

from tests.test_ik0383_prerequisite_followup import (  # noqa: F401  (sys.path を整える)
    PREREQ,
    TOPIC_TITLE,
    _chat,
    _course_data,
    _topic_info,
    chat_env,
)

import routes.learning as learning_mod  # noqa: E402
from api import services  # noqa: E402
from core import label_vocab  # noqa: E402
from core.dialogue_shape import GATE_SEPARATOR  # noqa: E402
from core.learning_support_agent import LearningSupportAgent  # noqa: E402

_EN_QUESTION = "What exactly is oscillating in baryon acoustic oscillations?"


def _en_gate_history():
    resp_text = (
        "説明本文" + GATE_SEPARATOR + label_vocab.PREREQUISITE_GATE_ANSWERED_MARKER_EN
        + learning_mod._prerequisite_gate_text(
            {"message": "", "first_prerequisite": PREREQ, "unlearned": [PREREQ]}, TOPIC_TITLE, "en"
        )
    )
    return [
        {"role": "user", "content": _EN_QUESTION},
        {"role": "assistant", "content": resp_text},
    ]


class TestFixedTextVocabulary:
    def test_markers_are_substrings_of_the_english_gate(self):
        text = label_vocab.PREREQUISITE_GATE_FULL_EN.format(topic="T", prerequisites="P", first="P")
        assert label_vocab.PREREQUISITE_GATE_MARKER_EN in text
        assert label_vocab.PREREQUISITE_GATE_FIRST_EN.format(first="P") in text
        assert '"T"' in text

    def test_english_gate_has_no_japanese_and_no_numbers(self):
        for template in (
            label_vocab.PREREQUISITE_GATE_FULL_EN,
            label_vocab.PREREQUISITE_GATE_ANSWERED_MARKER_EN,
            label_vocab.PREREQUISITE_CHOICE_YES_EN,
            label_vocab.PREREQUISITE_CHOICE_NO_LABEL_EN,
            label_vocab.PREREQUISITE_CHOICE_NO_MESSAGE_EN,
        ):
            assert not re.search(r"[\u3040-\u30ff\u3400-\u9fff]", template)
            assert not any(ch.isdigit() for ch in template)

    def test_language_is_read_from_the_utterance_only(self):
        assert learning_mod._fixed_text_language(_EN_QUESTION) == "en"
        assert learning_mod._fixed_text_language("Why?") == "en"
        assert learning_mod._fixed_text_language("主結果を教えて") == "ja"
        assert learning_mod._fixed_text_language("BAO の音速はどこに効きますか") == "ja"


class TestRoute:
    def test_english_question_gets_english_gate_only(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")
        resp = _chat(_EN_QUESTION)
        assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
        assert learning_mod.PREREQUISITE_GATE_ANSWERED_MARKER not in resp.answer
        tail = resp.answer.split(GATE_SEPARATOR, 1)[1]
        assert tail.startswith(label_vocab.PREREQUISITE_GATE_ANSWERED_MARKER_EN)
        assert f'To understand "{TOPIC_TITLE}"' in tail
        assert resp.shape == {"closing_question": None, "has_gate": True, "mirror_kept": False}
        labels = [a.label for a in resp.next_actions]
        assert label_vocab.PREREQUISITE_CHOICE_YES_EN in labels
        assert "はい、理解しています" not in labels

    def test_japanese_question_keeps_japanese_gate_byte_for_byte(self, chat_env, monkeypatch):
        monkeypatch.setattr(learning_mod, "_classify_intent", lambda *a, **k: "DOMAIN_RAG")
        resp = _chat("主結果を教えて")
        tail = resp.answer.split(GATE_SEPARATOR, 1)[1]
        assert tail.startswith(learning_mod.PREREQUISITE_GATE_ANSWERED_MARKER)
        assert learning_mod.PREREQUISITE_GATE_MARKER in tail
        assert label_vocab.PREREQUISITE_GATE_MARKER_EN not in resp.answer
        labels = [a.label for a in resp.next_actions]
        assert "はい、理解しています" in labels


class TestHistoryDetectorsReadEnglishGate:
    """RS5 の 4 本の判定器（skipped notice は既に日英両対応）が英語の逆質問を読む。"""

    def test_previous_turn_and_first_prerequisite(self):
        history = _en_gate_history()
        assert learning_mod._previous_turn_was_prerequisite_gate(history, TOPIC_TITLE) == PREREQ
        assert learning_mod._prerequisite_gate_asked_in_history(history, TOPIC_TITLE)

    def test_answered_marker_means_no_resume(self):
        # 回答済みの英語の逆質問に "yes" と答えても、元の質問を答え直さない。
        assert learning_mod._question_before_prerequisite_gate(_en_gate_history(), TOPIC_TITLE) is None

    def test_other_topic_is_not_matched(self):
        assert learning_mod._previous_turn_was_prerequisite_gate(_en_gate_history(), "別のトピック") is None

    def test_english_answers_to_the_gate(self):
        follow = lambda m: learning_mod._prerequisite_followup(  # noqa: E731
            m, _en_gate_history(), _topic_info(), TOPIC_TITLE, _course_data()
        )
        assert follow(label_vocab.PREREQUISITE_CHOICE_YES_EN) == ("after_gate", None)
        assert follow(
            label_vocab.PREREQUISITE_CHOICE_NO_MESSAGE_EN.format(prerequisite=PREREQ)
        ) == ("explain", PREREQ)
        assert services._is_explicit_prerequisite_acknowledgement(label_vocab.PREREQUISITE_CHOICE_YES_EN)

    def test_ack_only_reply_after_english_gate_is_english(self, chat_env):
        resp = _chat("Yes, I understand", _en_gate_history())
        assert resp.answer == label_vocab_ack(PREREQ)
        assert resp.shape == {"closing_question": None, "has_gate": False, "mirror_kept": False}


def label_vocab_ack(prereq: str) -> str:
    return learning_mod.PREREQUISITE_ACK_ONLY_REPLY_EN.format(prerequisite=prereq)


class TestChoiceActions:
    def test_english_choices(self):
        agent = LearningSupportAgent("course-1", _course_data())
        actions = agent.prerequisite_choice_actions(PREREQ, language="en")
        assert [a.message for a in actions] == [
            label_vocab.PREREQUISITE_CHOICE_YES_EN,
            label_vocab.PREREQUISITE_CHOICE_NO_MESSAGE_EN.format(prerequisite=PREREQ),
        ]

    def test_japanese_choices_unchanged(self):
        agent = LearningSupportAgent("course-1", _course_data())
        actions = agent.prerequisite_choice_actions(PREREQ)
        assert actions[0].label == "はい、理解しています"
        assert actions[1].message == f"{PREREQ}について教えてください"
