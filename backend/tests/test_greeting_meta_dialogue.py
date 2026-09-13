"""Issue #59: メタ対話（挨拶・学習開始）ハンドリングのテスト。

- 挨拶パターン判定ロジック

（``greeting_response_node`` / ``_route_after_analyzer`` / StudentGraph 構造の
テストは、本番ルートへ未接続のまま残っていた ``core/graphs/`` の撤去に伴って
削除した。挨拶の本番実装は ``api/routes/learning.py``。）
"""

from __future__ import annotations


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
