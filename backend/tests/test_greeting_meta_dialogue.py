"""Issue #59: メタ対話（挨拶・学習開始）ハンドリングのテスト。

- 挨拶パターン判定ロジック
- greeting_response_node のフォールバック動作
- _route_after_analyzer の分岐ロジック
- StudentGraph にGreetingResponseノードが組み込まれていること
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 1. 挨拶パターン判定テスト
# ---------------------------------------------------------------------------

# learning.py の _is_greeting / _GREETING_PATTERNS と同等のロジックを
# 直接テストする（learning.py は FastAPI + 多数の依存を import するため）。

_GREETING_PATTERNS = [
    "こんにちは", "こんばんは", "おはよう", "はじめまして",
    "よろしくお願い", "学習を始め", "学習を開始", "勉強を始め",
    "始めたい", "開始したい", "スタート",
    "第1章の学習を開始する", "前提知識を確認する",
]


def _is_greeting(message: str) -> bool:
    msg = message.strip()
    if len(msg) < 30 and any(p in msg for p in _GREETING_PATTERNS):
        return True
    return False


class TestIsGreeting:
    """メタ対話パターンの判定ロジック。"""

    def test_greeting_konnichiwa(self):
        assert _is_greeting("こんにちは") is True

    def test_greeting_start_learning(self):
        assert _is_greeting("学習を始めたい") is True

    def test_greeting_hajimemashite(self):
        assert _is_greeting("はじめまして") is True

    def test_greeting_yoroshiku(self):
        assert _is_greeting("よろしくお願いします") is True

    def test_greeting_start_chapter(self):
        assert _is_greeting("第1章の学習を開始する") is True

    def test_greeting_konbanwa(self):
        assert _is_greeting("こんばんは") is True

    def test_greeting_start_button_text(self):
        assert _is_greeting("スタート") is True

    def test_not_greeting_physics_question(self):
        assert _is_greeting("ゲージ対称性とは何ですか？") is False

    def test_not_greeting_formula_question(self):
        assert _is_greeting("シュレーディンガー方程式を導出してください") is False

    def test_not_greeting_long_message(self):
        """長いメッセージは挨拶とみなさない（30文字超）。"""
        long_msg = "こんにちは、今日はゲージ対称性について詳しく学びたいのですが、具体的にはどのような内容から始めればよいでしょうか？"
        assert _is_greeting(long_msg) is False

    def test_empty_string(self):
        assert _is_greeting("") is False

    def test_whitespace_only(self):
        assert _is_greeting("   ") is False


# ---------------------------------------------------------------------------
# 2. greeting_response_node テスト
# ---------------------------------------------------------------------------


class TestGreetingResponseNode:
    """GreetingResponse ノードの動作テスト。"""

    def test_fallback_on_llm_failure(self):
        """LLM呼び出し失敗時にフォールバックメッセージを返すこと。"""
        from core.graphs.student_graph import greeting_response_node

        with patch("core.graphs.student_graph.generate_text", side_effect=Exception("LLM error")):
            result = greeting_response_node({
                "question": "こんにちは",
                "course_title": "量子力学入門",
                "topic_title": "波動関数",
            })
            assert "raw_answer" in result
            assert "学習を始めましょう" in result["raw_answer"]

    def test_returns_raw_answer_on_success(self):
        """LLM呼び出し成功時にraw_answerを返すこと。"""
        from core.graphs.student_graph import greeting_response_node

        mock_response = "ようこそ！まずは波動関数について確認しましょうか？"
        with patch("core.graphs.student_graph.generate_text", return_value=mock_response):
            result = greeting_response_node({
                "question": "学習を始めたい",
                "course_title": "量子力学入門",
                "topic_title": "波動関数",
            })
            assert result["raw_answer"] == mock_response

    def test_uses_default_titles_when_missing(self):
        """コース・トピックタイトルが未設定時にデフォルト値を使うこと。"""
        from core.graphs.student_graph import greeting_response_node

        with patch("core.graphs.student_graph.generate_text", return_value="テスト") as mock_gen:
            greeting_response_node({"question": "こんにちは"})
            prompt = mock_gen.call_args[1]["messages"][0]["content"]
            assert "未選択コース" in prompt
            assert "最初のトピック" in prompt


# ---------------------------------------------------------------------------
# 3. _route_after_analyzer テスト
# ---------------------------------------------------------------------------


class TestRouteAfterAnalyzer:
    """QueryAnalyzer 後の分岐ロジック。"""

    def test_greeting_routes_to_greeting(self):
        from core.graphs.student_graph import _route_after_analyzer

        assert _route_after_analyzer({"intent": "greeting"}) == "greeting"

    def test_factual_routes_to_retrieval(self):
        from core.graphs.student_graph import _route_after_analyzer

        assert _route_after_analyzer({"intent": "factual"}) == "retrieval"

    def test_other_routes_to_retrieval(self):
        from core.graphs.student_graph import _route_after_analyzer

        assert _route_after_analyzer({"intent": "other"}) == "retrieval"

    def test_conceptual_routes_to_retrieval(self):
        from core.graphs.student_graph import _route_after_analyzer

        assert _route_after_analyzer({"intent": "conceptual"}) == "retrieval"

    def test_missing_intent_routes_to_retrieval(self):
        from core.graphs.student_graph import _route_after_analyzer

        assert _route_after_analyzer({}) == "retrieval"


# ---------------------------------------------------------------------------
# 4. StudentGraph 構造テスト (greeting ノード統合)
# ---------------------------------------------------------------------------


class TestStudentGraphGreetingIntegration:
    """StudentGraph に greeting_response ノードが正しく組み込まれていること。"""

    @pytest.fixture()
    def graph(self):
        from core.graphs.student_graph import build_student_graph

        return build_student_graph()

    def test_graph_compiles_with_greeting(self, graph):
        """greeting ノード追加後もグラフがコンパイルできること。"""
        assert graph is not None

    def test_query_analyzer_detects_greeting(self):
        """QueryAnalyzer が greeting インテントを返すこと。"""
        from core.graphs.student_graph import query_analyzer_node

        mock_response = json.dumps({"intent": "greeting", "search_keywords": []})
        with patch("core.graphs.student_graph.generate_text", return_value=mock_response):
            result = query_analyzer_node({"question": "こんにちは"})
            assert result["intent"] == "greeting"
            assert result["search_keywords"] == []

    def test_query_analyzer_non_greeting(self):
        """通常の質問では greeting 以外のインテントを返すこと。"""
        from core.graphs.student_graph import query_analyzer_node

        mock_response = json.dumps({"intent": "conceptual", "search_keywords": ["ゲージ対称性"]})
        with patch("core.graphs.student_graph.generate_text", return_value=mock_response):
            result = query_analyzer_node({"question": "ゲージ対称性とは？"})
            assert result["intent"] == "conceptual"
            assert result["search_keywords"] == ["ゲージ対称性"]
