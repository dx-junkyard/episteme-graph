"""core/llm_worker/history.py::window_history の単体テスト。

正本: docs/features/assistant_common_infra_design.md §2。チャット型4者
（学習チャット本体・グラフ要素説明・コースビルダー・W層対話）+ Admin Copilot の
会話履歴前処理を一般化した共通ユーティリティの振る舞いを検証する。
"""

from __future__ import annotations

from core.llm_worker.history import window_history


class TestRoleAndContentFiltering:
    def test_non_dict_items_are_skipped(self):
        history = [{"role": "user", "content": "a"}, "not a dict", 123, None]
        result = window_history(history)
        assert result == [{"role": "user", "content": "a"}]

    def test_unknown_role_is_skipped(self):
        history = [
            {"role": "system", "content": "sys prompt"},
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "tool output"},
        ]
        result = window_history(history)
        assert result == [{"role": "user", "content": "hi"}]

    def test_missing_role_is_skipped(self):
        history = [{"content": "no role"}, {"role": "assistant", "content": "ok"}]
        result = window_history(history)
        assert result == [{"role": "assistant", "content": "ok"}]

    def test_empty_content_is_skipped(self):
        history = [
            {"role": "user", "content": ""},
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": "real content"},
        ]
        result = window_history(history)
        assert result == [{"role": "assistant", "content": "real content"}]

    def test_none_content_is_skipped(self):
        history = [{"role": "user", "content": None}, {"role": "user", "content": "hi"}]
        result = window_history(history)
        assert result == [{"role": "user", "content": "hi"}]

    def test_non_string_content_is_stringified(self):
        history = [{"role": "user", "content": 42}]
        result = window_history(history)
        assert result == [{"role": "user", "content": "42"}]

    def test_output_only_has_role_and_content_keys(self):
        history = [{"role": "user", "content": "hi", "extra": "dropped", "id": 7}]
        result = window_history(history)
        assert result == [{"role": "user", "content": "hi"}]
        assert set(result[0].keys()) == {"role", "content"}


class TestTrimming:
    def test_content_is_stripped(self):
        history = [{"role": "user", "content": "  hello  "}]
        result = window_history(history)
        assert result == [{"role": "user", "content": "hello"}]

    def test_content_is_trimmed_to_max_chars(self):
        history = [{"role": "user", "content": "x" * 100}]
        result = window_history(history, max_chars=10)
        assert result == [{"role": "user", "content": "x" * 10}]

    def test_max_chars_default_is_2000(self):
        history = [{"role": "user", "content": "y" * 3000}]
        result = window_history(history)
        assert len(result[0]["content"]) == 2000


class TestCurrentMessageDeduplication:
    def test_trailing_duplicate_user_message_is_removed(self):
        history = [
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": "current question"},
        ]
        result = window_history(history, current_message="current question")
        assert result == [{"role": "assistant", "content": "earlier answer"}]

    def test_comparison_happens_after_trim(self):
        history = [{"role": "user", "content": "  same  "}]
        result = window_history(history, current_message="same")
        assert result == []

    def test_trailing_assistant_message_is_not_removed(self):
        history = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "same"},
        ]
        result = window_history(history, current_message="same")
        assert result == [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "same"},
        ]

    def test_non_matching_trailing_user_message_is_kept(self):
        history = [{"role": "user", "content": "different question"}]
        result = window_history(history, current_message="current question")
        assert result == [{"role": "user", "content": "different question"}]

    def test_current_message_none_does_not_dedupe(self):
        history = [{"role": "user", "content": "hi"}]
        result = window_history(history, current_message=None)
        assert result == [{"role": "user", "content": "hi"}]

    def test_current_message_empty_string_does_not_dedupe(self):
        history = [{"role": "user", "content": "hi"}]
        result = window_history(history, current_message="   ")
        assert result == [{"role": "user", "content": "hi"}]


class TestHeadKeepAndWindowing:
    def test_all_kept_when_at_or_below_threshold(self):
        history = [{"role": "user", "content": str(i)} for i in range(5)]
        result = window_history(history, max_messages=5, head_keep=0)
        assert len(result) == 5
        assert [m["content"] for m in result] == ["0", "1", "2", "3", "4"]

    def test_all_kept_exactly_at_head_keep_plus_max_messages(self):
        history = [{"role": "user", "content": str(i)} for i in range(7)]
        result = window_history(history, max_messages=5, head_keep=2)
        assert len(result) == 7
        assert [m["content"] for m in result] == [str(i) for i in range(7)]

    def test_windowed_when_exceeding_threshold_no_head_keep(self):
        history = [{"role": "user", "content": str(i)} for i in range(30)]
        result = window_history(history, max_messages=20, head_keep=0)
        assert len(result) == 20
        assert [m["content"] for m in result] == [str(i) for i in range(10, 30)]

    def test_windowed_when_exceeding_threshold_with_head_keep(self):
        history = [{"role": "user", "content": str(i)} for i in range(10)]
        # head_keep=2, max_messages=5 -> threshold 7; len=10 > 7, so windowed.
        result = window_history(history, max_messages=5, head_keep=2)
        contents = [m["content"] for m in result]
        # first 2 kept + last 5 kept, no overlap/duplication
        assert contents == ["0", "1", "5", "6", "7", "8", "9"]
        assert len(contents) == len(set(contents))

    def test_head_keep_boundary_no_duplication(self):
        """len == head_keep + max_messages + 1 is the smallest case where windowing
        kicks in; head and tail slices must not overlap or duplicate an item."""
        history = [{"role": "user", "content": str(i)} for i in range(8)]
        result = window_history(history, max_messages=5, head_keep=2)
        contents = [m["content"] for m in result]
        assert contents == ["0", "1", "3", "4", "5", "6", "7"]
        assert len(contents) == len(set(contents))


class TestEmptyOrNoneInput:
    def test_none_history_returns_empty_list(self):
        assert window_history(None) == []

    def test_empty_history_returns_empty_list(self):
        assert window_history([]) == []

    def test_history_that_filters_to_nothing_returns_empty_list(self):
        assert window_history([{"role": "system", "content": "x"}]) == []
