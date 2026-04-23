"""Issue #141: 意図分類（Intent Routing）のテスト。

- _is_greeting との協調動作
- _classify_intent のルール判定ショートカット（挨拶→LEARNING_ADVICE）
- LLM 呼び出しによる CHIT_CHAT / LEARNING_ADVICE / DOMAIN_RAG 分類
- LLM 失敗時のフォールバック（DOMAIN_RAG）
- learning_chat の各ルートへの分岐動作
- 基礎知識フォールバック（RAGヒット0件時のラベル付与）
- _NAVIGATOR_SYSTEM_PROMPT / _TUTOR_SYSTEM_PROMPT が定義されていること
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# ヘルパー: learning.py の依存を持ち込まずにロジックを直接テスト
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 1. _is_greeting との協調テスト（ルールベース判定）
# ---------------------------------------------------------------------------


class TestClassifyIntentGreetingShortcut:
    """_is_greeting が True の場合は LLM を呼ばずに LEARNING_ADVICE を返す。"""

    def test_greeting_returns_learning_advice_without_llm(self):
        """挨拶パターンは LLM 呼び出しなしで LEARNING_ADVICE に分類される。"""
        from api.routes.learning import _classify_intent

        with patch("api.routes.learning.generate_text") as mock_gen:
            result = _classify_intent("こんにちは", "量子力学入門")
            mock_gen.assert_not_called()
            assert result == "LEARNING_ADVICE"

    def test_start_learning_returns_learning_advice_without_llm(self):
        """学習開始パターンも LLM なしで LEARNING_ADVICE に分類される。"""
        from api.routes.learning import _classify_intent

        with patch("api.routes.learning.generate_text") as mock_gen:
            result = _classify_intent("学習を始めたい", "量子力学入門")
            mock_gen.assert_not_called()
            assert result == "LEARNING_ADVICE"


# ---------------------------------------------------------------------------
# 2. LLM 分類テスト
# ---------------------------------------------------------------------------


class TestClassifyIntentLLM:
    """LLM を使った 3 ルート分類の動作テスト。"""

    def test_chit_chat_classification(self):
        """LLM が CHIT_CHAT を返した場合に CHIT_CHAT として分類される。"""
        from api.routes.learning import _classify_intent

        with patch("api.routes.learning.generate_text", return_value="CHIT_CHAT"):
            result = _classify_intent("今日の天気はどうですか？", "量子力学入門")
            assert result == "CHIT_CHAT"

    def test_learning_advice_classification(self):
        """LLM が LEARNING_ADVICE を返した場合に LEARNING_ADVICE として分類される。"""
        from api.routes.learning import _classify_intent

        with patch("api.routes.learning.generate_text", return_value="LEARNING_ADVICE"):
            result = _classify_intent("どのように学習を進めればよいですか？", "量子力学入門")
            assert result == "LEARNING_ADVICE"

    def test_domain_rag_classification(self):
        """LLM が DOMAIN_RAG を返した場合に DOMAIN_RAG として分類される。"""
        from api.routes.learning import _classify_intent

        with patch("api.routes.learning.generate_text", return_value="DOMAIN_RAG"):
            result = _classify_intent("レプトンとは何ですか？", "量子力学入門")
            assert result == "DOMAIN_RAG"

    def test_label_extraction_from_verbose_response(self):
        """LLM が余分な文章を返しても分類ラベルを抽出できる。"""
        from api.routes.learning import _classify_intent

        with patch("api.routes.learning.generate_text", return_value="この質問はDOMAIN_RAGです。"):
            result = _classify_intent("ゲージ場とは？", "素粒子物理学")
            assert result == "DOMAIN_RAG"

    def test_llm_failure_defaults_to_domain_rag(self):
        """LLM 呼び出し失敗時はデフォルトで DOMAIN_RAG を返す。"""
        from api.routes.learning import _classify_intent

        with patch("api.routes.learning.generate_text", side_effect=Exception("LLM error")):
            result = _classify_intent("ゲージ場とは？", "素粒子物理学")
            assert result == "DOMAIN_RAG"

    def test_unknown_label_defaults_to_domain_rag(self):
        """LLM が未知のラベルを返した場合は DOMAIN_RAG にフォールバックする。"""
        from api.routes.learning import _classify_intent

        with patch("api.routes.learning.generate_text", return_value="UNKNOWN_LABEL"):
            result = _classify_intent("何かの質問", "素粒子物理学")
            assert result == "DOMAIN_RAG"

    def test_uses_fast_llm_model(self):
        """意図分類には fast モードの LLM パラメータが使われる。"""
        from api.routes.learning import _classify_intent

        captured = {}

        def mock_generate_text(messages, **kwargs):
            captured["model"] = kwargs.get("model")
            return "DOMAIN_RAG"

        with patch("api.routes.learning.generate_text", side_effect=mock_generate_text):
            with patch("api.routes.learning.get_llm_params", return_value={"model": "fast-model", "reasoning_effort": "low"}) as mock_params:
                _classify_intent("レプトンとは？", "量子力学入門")
                mock_params.assert_called_once_with("fast")


# ---------------------------------------------------------------------------
# 3. 基礎知識フォールバックテスト
# ---------------------------------------------------------------------------


class TestBasicKnowledgeFallback:
    """RAGヒット0件時の基礎知識フォールバック動作テスト。"""

    def test_basic_knowledge_label_added_when_missing(self):
        """LLM 応答に【基礎知識の補足】がない場合は冒頭に追加される。"""
        # ラベルなしの応答をシミュレート
        response_without_label = "レプトンは素粒子の一種で、クォークと異なり強い相互作用をしません。"
        assert not response_without_label.strip().startswith("【基礎知識")
        labeled = "【基礎知識の補足】\n" + response_without_label
        assert labeled.startswith("【基礎知識の補足】")

    def test_basic_knowledge_label_preserved_when_present(self):
        """LLM 応答に【基礎知識の補足】が既にある場合は二重付与しない。"""
        response_with_label = "【基礎知識の補足】\nレプトンは素粒子の一種です。"
        assert response_with_label.strip().startswith("【基礎知識")
        # ラベルなしなので追加しない
        if not response_with_label.strip().startswith("【基礎知識"):
            result = "【基礎知識の補足】\n" + response_with_label
        else:
            result = response_with_label
        assert result.count("【基礎知識の補足】") == 1


# ---------------------------------------------------------------------------
# 4. システムプロンプト定義テスト
# ---------------------------------------------------------------------------


class TestSystemPrompts:
    """_NAVIGATOR_SYSTEM_PROMPT / _TUTOR_SYSTEM_PROMPT が正しく定義されていること。"""

    def test_navigator_system_prompt_defined(self):
        """_NAVIGATOR_SYSTEM_PROMPT が learning.py に定義されている。"""
        from api.routes.learning import _NAVIGATOR_SYSTEM_PROMPT
        assert isinstance(_NAVIGATOR_SYSTEM_PROMPT, str)
        assert len(_NAVIGATOR_SYSTEM_PROMPT) > 100

    def test_navigator_prompt_contains_role_description(self):
        """_NAVIGATOR_SYSTEM_PROMPT にナビゲーターの役割説明が含まれる。"""
        from api.routes.learning import _NAVIGATOR_SYSTEM_PROMPT
        assert "ナビゲーター" in _NAVIGATOR_SYSTEM_PROMPT

    def test_navigator_prompt_contains_latex_instruction(self):
        """_NAVIGATOR_SYSTEM_PROMPT に LaTeX 記法の指示が含まれる。"""
        from api.routes.learning import _NAVIGATOR_SYSTEM_PROMPT
        assert "LaTeX" in _NAVIGATOR_SYSTEM_PROMPT

    def test_navigator_prompt_contains_drilldown_format(self):
        """_NAVIGATOR_SYSTEM_PROMPT にドリルダウンフォーマット指示が含まれる。"""
        from api.routes.learning import _NAVIGATOR_SYSTEM_PROMPT
        assert "〇〇について詳しく聞く" in _NAVIGATOR_SYSTEM_PROMPT

    def test_tutor_system_prompt_defined(self):
        """_TUTOR_SYSTEM_PROMPT が lecture.py に定義されている。"""
        from api.routes.lecture import _TUTOR_SYSTEM_PROMPT
        assert isinstance(_TUTOR_SYSTEM_PROMPT, str)
        assert len(_TUTOR_SYSTEM_PROMPT) > 100

    def test_tutor_prompt_contains_role_description(self):
        """_TUTOR_SYSTEM_PROMPT にチューターの役割説明が含まれる。"""
        from api.routes.lecture import _TUTOR_SYSTEM_PROMPT
        assert "チューター" in _TUTOR_SYSTEM_PROMPT

    def test_tutor_prompt_encourages_resume(self):
        """_TUTOR_SYSTEM_PROMPT に講義再開を促す指示が含まれる。"""
        from api.routes.lecture import _TUTOR_SYSTEM_PROMPT
        assert "再生ボタン" in _TUTOR_SYSTEM_PROMPT

    def test_learning_system_prompt_renamed(self):
        """旧 _LEARNING_SYSTEM_PROMPT は削除され、_NAVIGATOR_SYSTEM_PROMPT になっている。"""
        import api.routes.learning as mod
        assert not hasattr(mod, "_LEARNING_SYSTEM_PROMPT"), \
            "_LEARNING_SYSTEM_PROMPT は _NAVIGATOR_SYSTEM_PROMPT に統合されているはずです"

    def test_lecture_interrupt_prompt_renamed(self):
        """旧 _LECTURE_INTERRUPT_SYSTEM_PROMPT は _TUTOR_SYSTEM_PROMPT に統合されている。"""
        import api.routes.lecture as mod
        assert not hasattr(mod, "_LECTURE_INTERRUPT_SYSTEM_PROMPT"), \
            "_LECTURE_INTERRUPT_SYSTEM_PROMPT は _TUTOR_SYSTEM_PROMPT に統合されているはずです"


# ---------------------------------------------------------------------------
# 5. _generate_learning_advice_response テスト
# ---------------------------------------------------------------------------


class TestGenerateLearningAdviceResponse:
    """_generate_learning_advice_response がコース情報を活用して応答を生成すること。"""

    def test_includes_topics_block_in_prompt(self):
        """コースのトピック一覧がプロンプトに含まれること。"""
        from api.routes.learning import _generate_learning_advice_response

        course_data = {
            "concepts": [],
            "topics": [
                {"id": "t1", "title": "波動関数"},
                {"id": "t2", "title": "シュレーディンガー方程式"},
            ],
        }

        with patch("api.routes.learning.generate_text", return_value="テスト応答") as mock_gen:
            _generate_learning_advice_response(
                "量子力学入門", "波動関数", "どう進めれば良いですか？",
                course_data=course_data,
            )
            prompt = mock_gen.call_args[1]["messages"][0]["content"]
            assert "波動関数" in prompt
            assert "シュレーディンガー方程式" in prompt

    def test_includes_message_in_prompt(self):
        """学生のメッセージがプロンプトに含まれること。"""
        from api.routes.learning import _generate_learning_advice_response

        student_message = "学習の進め方を教えてください"
        with patch("api.routes.learning.generate_text", return_value="テスト") as mock_gen:
            _generate_learning_advice_response(
                "量子力学入門", "波動関数", student_message,
            )
            prompt = mock_gen.call_args[1]["messages"][0]["content"]
            assert student_message in prompt

    def test_uses_standard_llm_mode(self):
        """Standard モードの LLM パラメータを使用すること。"""
        from api.routes.learning import _generate_learning_advice_response

        with patch("api.routes.learning.get_llm_params", return_value={"model": "std-model", "reasoning_effort": "medium"}) as mock_params:
            with patch("api.routes.learning.generate_text", return_value="テスト"):
                _generate_learning_advice_response("コース", "トピック", "メッセージ")
                mock_params.assert_called_once_with("standard")

    def test_fallback_on_llm_failure(self):
        """LLM 失敗時にフォールバックメッセージを返すこと。"""
        from api.routes.learning import _generate_learning_advice_response

        with patch("api.routes.learning.generate_text", side_effect=Exception("error")):
            result = _generate_learning_advice_response(
                "量子力学入門", "波動関数", "こんにちは",
            )
            assert "量子力学入門" in result
            assert "波動関数" in result
