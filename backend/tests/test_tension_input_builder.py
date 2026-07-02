"""TensionMiningAgent input_builder — 窓の切り出し境界・tutor短縮・id整合・文字数上限。"""

from __future__ import annotations

from core.tension.input_builder import (
    MAX_INPUT_CHARS,
    MAX_WINDOW_TURNS,
    TUTOR_TEXT_LIMIT,
    build_user_content,
    select_window_turns,
    turns_from_history,
)
from core.tension.schema import ConversationTurn, ConversationWindow


def _history(n_pairs: int) -> list[dict]:
    h = []
    for i in range(n_pairs):
        h.append({"role": "user", "content": f"質問{i}です"})
        h.append({"role": "assistant", "content": f"回答{i}です"})
    return h


class TestTurnsFromHistory:
    def test_roles_and_deterministic_ids(self):
        turns = turns_from_history(_history(2))
        assert [t.turn_id for t in turns] == ["msg_0000", "msg_0001", "msg_0002", "msg_0003"]
        assert [t.role for t in turns] == ["learner", "tutor", "learner", "tutor"]

    def test_hint_matching_with_truncated_payload_text(self):
        # record_interest_trace は text を 500 字で切るため前方一致で照合する
        long_text = "あ" * 600
        history = [{"role": "user", "content": long_text}]
        turns = turns_from_history(history, hint_texts=[long_text[:500]])
        assert turns[0].tension_hint is True

    def test_hint_never_set_on_tutor(self):
        history = [{"role": "assistant", "content": "回答"}]
        turns = turns_from_history(history, hint_texts=["回答"])
        assert turns[0].tension_hint is False


class TestSelectWindow:
    def test_window_spans_hint_plus_minus_3_exchanges(self):
        turns = turns_from_history(_history(10))
        turns[10].tension_hint = True  # 6往復目の learner 発話
        selected = select_window_turns(turns)
        ids = [t.turn_id for t in selected]
        assert "msg_0010" in ids
        assert ids[0] == "msg_0004"   # 前3往復 = 6 turn 前
        assert ids[-1] == "msg_0016"  # 後3往復 = 6 turn 後

    def test_nearby_hints_are_merged_into_one_window(self):
        turns = turns_from_history(_history(10))
        turns[6].tension_hint = True
        turns[10].tension_hint = True
        selected = select_window_turns(turns)
        ids = [t.turn_id for t in selected]
        # 連結された1つの窓（重複なし・昇順）
        assert ids == sorted(set(ids))
        assert "msg_0006" in ids and "msg_0010" in ids

    def test_window_is_capped_at_max_turns_keeping_newest(self):
        turns = turns_from_history(_history(30))
        for i in range(0, 60, 2):
            turns[i].tension_hint = True
        selected = select_window_turns(turns)
        assert len(selected) <= MAX_WINDOW_TURNS
        assert selected[-1].turn_id == turns[-1].turn_id  # 最新側を優先して残す

    def test_no_hint_returns_tail(self):
        turns = turns_from_history(_history(30))
        selected = select_window_turns(turns)
        assert len(selected) == MAX_WINDOW_TURNS
        assert selected[-1].turn_id == "msg_0059"

    def test_empty_turns(self):
        assert select_window_turns([]) == []


class TestBuildUserContent:
    def _window(self, turns):
        return ConversationWindow(
            course_id="c1", topic_id="t1", topic_title="トピック", turns=turns,
            components=[{"id": "tc_1", "label": "HQET"}], chunks=[{"id": "ch_1", "head": "..."}],
        )

    def test_format_contains_session_header_and_turn_attrs(self):
        turns = [
            ConversationTurn("msg_0000", "learner", "質問です", tension_hint=True),
            ConversationTurn("msg_0001", "tutor", "回答です", tier="approved"),
        ]
        content = build_user_content(self._window(turns), max_candidates=3)
        assert "course_id: c1" in content
        assert "topic_title: トピック" in content
        assert "[turn id=msg_0000 role=learner tension_hint=true]" in content
        assert "[turn id=msg_0001 role=tutor tier=approved]" in content
        assert "max_candidates = 3" in content
        assert "tc_1" in content and "ch_1" in content

    def test_tutor_text_truncated_to_400(self):
        turns = [
            ConversationTurn("msg_0000", "learner", "質問"),
            ConversationTurn("msg_0001", "tutor", "長" * 1000),
        ]
        content = build_user_content(self._window(turns), max_candidates=3)
        assert "長" * (TUTOR_TEXT_LIMIT + 1) not in content
        assert "長" * TUTOR_TEXT_LIMIT + "…" in content

    def test_learner_text_is_never_truncated(self):
        # evidence_quote の逐語一致検証があるため learner 発話は原文のまま渡す
        long_learner = "問" * 800
        turns = [ConversationTurn("msg_0000", "learner", long_learner)]
        content = build_user_content(self._window(turns), max_candidates=3)
        assert long_learner in content

    def test_char_cap_drops_oldest_turns_first(self):
        turns = []
        for i in range(40):
            turns.append(ConversationTurn(f"msg_{2*i:04d}", "learner", f"質問{i}" + "あ" * 300))
            turns.append(ConversationTurn(f"msg_{2*i+1:04d}", "tutor", f"回答{i}" + "い" * 500))
        content = build_user_content(self._window(turns), max_candidates=3)
        assert len(content) <= MAX_INPUT_CHARS
        # 最新 turn は必ず残る
        assert "msg_0079" in content
