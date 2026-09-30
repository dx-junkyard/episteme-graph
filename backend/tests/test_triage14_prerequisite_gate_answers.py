"""TRIAGE14（第 14 周）: 前提確認の逆質問が学習者の最初の具体的な質問を吸わない。

- (a) 逆質問は 1 コース × 1 トピック × 1 学習者につき一度だけ（提示の記録で抑止。能力推定なし = UC5）。
- (c) 逆質問を出すターンでも質問には答え、逆質問は回答の後ろに添える（LLM は通常の往復と同じ 1 回）。
- 回答済みの逆質問に「理解している」とだけ答えたら、元の質問を答え直さず固定文（LLM 0 回）。
"""

from __future__ import annotations

from tests.test_ik0383_prerequisite_followup import (  # noqa: F401  (sys.path を整える)
    PREREQ,
    TOPIC_TITLE,
    _chat,
    _gate_text,
    chat_env,
)

import routes.learning as learning_mod  # noqa: E402


def _answered_gate_history():
    return [
        {"role": "user", "content": "µ0≈1.04 の出所は？"},
        {
            "role": "assistant",
            "content": "説明本文\n\n---\n\n" + learning_mod.PREREQUISITE_GATE_ANSWERED_MARKER + _gate_text(),
        },
    ]


def test_first_gate_turn_answers_question_and_appends_gate(chat_env):
    resp = _chat("µ0≈1.04 の出所は？")
    assert "説明本文\n\n---" in resp.answer
    assert learning_mod.PREREQUISITE_GATE_MARKER in resp.answer
    assert learning_mod.PREREQUISITE_GATE_ANSWERED_MARKER in resp.answer
    assert "µ0≈1.04 の出所は？" in chat_env.prompts[-1]
    assert len(chat_env.prompts) == 1
    labels = [a.label for a in resp.next_actions]
    assert "はい、理解しています" in labels
    assert chat_env.presented == {"t3"}


def test_gate_not_shown_again_once_presented(chat_env):
    chat_env.presented.add("t3")
    resp = _chat("主結果を教えて")
    assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
    assert resp.answer.endswith("説明本文")


def test_ack_after_answered_gate_returns_fixed_reply_without_llm(chat_env):
    resp = _chat("はい、理解しています", _answered_gate_history())
    assert PREREQ in resp.answer
    assert chat_env.prompts == []
    assert chat_env.gate_calls == ["はい、理解しています"]


def test_ordinary_question_after_answered_gate_is_answered(chat_env):
    resp = _chat("音速はどこに効きますか", _answered_gate_history())
    assert learning_mod.PREREQUISITE_GATE_MARKER not in resp.answer
    assert "音速はどこに効きますか" in chat_env.prompts[-1]


def test_resume_is_skipped_for_answered_gate():
    assert learning_mod._question_before_prerequisite_gate(_answered_gate_history(), TOPIC_TITLE) is None
