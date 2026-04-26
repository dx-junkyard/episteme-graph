"""Issue #141/#143: 意図分類（Intent Routing）と動的プロンプト生成のテスト。

Issue #141:
- _is_greeting との協調動作
- _classify_intent のルール判定ショートカット（挨拶→LEARNING_ADVICE）
- LLM 呼び出しによる CHIT_CHAT / LEARNING_ADVICE / DOMAIN_RAG 分類
- LLM 失敗時のフォールバック（DOMAIN_RAG）
- learning_chat の各ルートへの分岐動作
- 基礎知識フォールバック（RAGヒット0件時のラベル付与）

Issue #143:
- _get_navigator_system_prompt(domain) が domain を埋め込んだプロンプトを返す
- _get_tutor_system_prompt(domain) が domain を埋め込んだプロンプトを返す
- domain が空の場合はフォールバック文言を使う
- _COURSE_BUILDER_SYSTEM_PROMPT に domain フィールドの定義が含まれる
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

    def test_prerequisite_confirmation_yes_returns_domain_rag_without_llm(self):
        """前提知識を理解している確認は、現在トピックの説明へ進む。"""
        from api.routes.learning import _classify_intent

        with patch("api.routes.learning.generate_text") as mock_gen:
            result = _classify_intent("はい、理解しています", "量子力学入門")
            mock_gen.assert_not_called()
            assert result == "DOMAIN_RAG"


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
# 3. RAGコンテキスト統合テスト（旧: 基礎知識フォールバック）
# ---------------------------------------------------------------------------


class TestRagContextIntegration:
    """RAGヒットあり/なし問わず単一パイプラインで処理されることのテスト。"""

    def test_no_basic_knowledge_label_in_system_prompt(self):
        """統合チュータープロンプトに「基礎知識の補足」ラベルを付与する指示がないこと。"""
        from api.routes.learning import _get_integrated_tutor_system_prompt
        prompt = _get_integrated_tutor_system_prompt("物理学")
        # プロンプトは「付けるな」という禁止指示として言及するが、付与を促す記述はない
        assert "絶対に付けないでください" in prompt

    def test_integrated_prompt_no_rejection_language(self):
        """統合チュータープロンプトに「教材にありません」等の拒絶表現が含まれないこと。"""
        from api.routes.learning import _get_integrated_tutor_system_prompt
        prompt = _get_integrated_tutor_system_prompt("物理学")
        assert "お答えできません" not in prompt
        assert "範囲外" not in prompt


# ---------------------------------------------------------------------------
# 4. システムプロンプト定義テスト
# ---------------------------------------------------------------------------


class TestSystemPrompts:
    """_get_navigator_system_prompt / _get_tutor_system_prompt が正しく定義されていること。"""

    # --- ナビゲーター ---

    def test_integrated_tutor_prompt_function_defined(self):
        """_get_integrated_tutor_system_prompt 関数が learning.py に定義されている。"""
        from api.routes.learning import _get_integrated_tutor_system_prompt
        assert callable(_get_integrated_tutor_system_prompt)

    def test_integrated_tutor_prompt_returns_string(self):
        """_get_integrated_tutor_system_prompt は str を返す。"""
        from api.routes.learning import _get_integrated_tutor_system_prompt
        result = _get_integrated_tutor_system_prompt("素粒子物理学")
        assert isinstance(result, str)
        assert len(result) > 100

    def test_integrated_tutor_prompt_contains_role_description(self):
        """生成プロンプトに親切な専属チューターの役割説明が含まれる。"""
        from api.routes.learning import _get_integrated_tutor_system_prompt
        assert "チューター" in _get_integrated_tutor_system_prompt("量子力学")

    def test_integrated_tutor_prompt_contains_domain(self):
        """生成プロンプトに指定した domain が含まれる。"""
        from api.routes.learning import _get_integrated_tutor_system_prompt
        prompt = _get_integrated_tutor_system_prompt("経済学")
        assert "経済学" in prompt

    def test_integrated_tutor_prompt_contains_latex_instruction(self):
        """生成プロンプトに LaTeX 記法の指示が含まれる。"""
        from api.routes.learning import _get_integrated_tutor_system_prompt
        assert "LaTeX" in _get_integrated_tutor_system_prompt("数学")

    def test_integrated_tutor_prompt_contains_drilldown_format(self):
        """生成プロンプトにドリルダウンフォーマット指示が含まれる。"""
        from api.routes.learning import _get_integrated_tutor_system_prompt
        assert "〇〇について詳しく聞く" in _get_integrated_tutor_system_prompt("物理学")

    def test_integrated_tutor_prompt_fallback_when_empty_domain(self):
        """domain が空文字の場合はフォールバック文言が使われる。"""
        from api.routes.learning import _get_integrated_tutor_system_prompt
        prompt = _get_integrated_tutor_system_prompt("")
        assert "このコースの専門分野" in prompt

    def test_integrated_tutor_prompt_no_warning_label_instruction(self):
        """プロンプトに「基礎知識の補足」ラベルを付けない旨の指示が含まれる。"""
        from api.routes.learning import _get_integrated_tutor_system_prompt
        prompt = _get_integrated_tutor_system_prompt("物理学")
        assert "基礎知識の補足" in prompt  # 付けないよう禁止する指示として含まれる

    # --- チューター ---

    def test_tutor_prompt_function_defined(self):
        """_get_tutor_system_prompt 関数が lecture.py に定義されている。"""
        from api.routes.lecture import _get_tutor_system_prompt
        assert callable(_get_tutor_system_prompt)

    def test_tutor_prompt_returns_string(self):
        """_get_tutor_system_prompt は str を返す。"""
        from api.routes.lecture import _get_tutor_system_prompt
        result = _get_tutor_system_prompt("素粒子物理学")
        assert isinstance(result, str)
        assert len(result) > 100

    def test_tutor_prompt_contains_role_description(self):
        """生成プロンプトにチューターの役割説明が含まれる。"""
        from api.routes.lecture import _get_tutor_system_prompt
        assert "チューター" in _get_tutor_system_prompt("量子力学")

    def test_tutor_prompt_contains_domain(self):
        """生成プロンプトに指定した domain が含まれる。"""
        from api.routes.lecture import _get_tutor_system_prompt
        prompt = _get_tutor_system_prompt("経済学")
        assert "経済学" in prompt

    def test_tutor_prompt_encourages_resume(self):
        """生成プロンプトに講義再開を促す指示が含まれる。"""
        from api.routes.lecture import _get_tutor_system_prompt
        assert "再生ボタン" in _get_tutor_system_prompt("物理学")

    def test_tutor_prompt_fallback_when_empty_domain(self):
        """domain が空文字の場合はフォールバック文言が使われる。"""
        from api.routes.lecture import _get_tutor_system_prompt
        prompt = _get_tutor_system_prompt("")
        assert "このコースの専門分野" in prompt

    # --- 旧定数の廃止確認 ---

    def test_learning_system_prompt_constant_removed(self):
        """旧 _LEARNING_SYSTEM_PROMPT 定数は削除されている。"""
        import api.routes.learning as mod
        assert not hasattr(mod, "_LEARNING_SYSTEM_PROMPT"), \
            "_LEARNING_SYSTEM_PROMPT は _get_integrated_tutor_system_prompt() に統合されているはずです"

    def test_navigator_system_prompt_removed(self):
        """_get_navigator_system_prompt は _get_integrated_tutor_system_prompt に統合されている。"""
        import api.routes.learning as mod
        assert not hasattr(mod, "_get_navigator_system_prompt"), \
            "_get_navigator_system_prompt は _get_integrated_tutor_system_prompt() に置き換えられているはずです"

    def test_lecture_interrupt_prompt_constant_removed(self):
        """旧 _LECTURE_INTERRUPT_SYSTEM_PROMPT 定数は削除されている。"""
        import api.routes.lecture as mod
        assert not hasattr(mod, "_LECTURE_INTERRUPT_SYSTEM_PROMPT"), \
            "_LECTURE_INTERRUPT_SYSTEM_PROMPT は _get_tutor_system_prompt() に統合されているはずです"

    def test_tutor_prompt_constant_removed(self):
        """_TUTOR_SYSTEM_PROMPT 定数ではなく関数になっている。"""
        import api.routes.lecture as mod
        assert not hasattr(mod, "_TUTOR_SYSTEM_PROMPT"), \
            "_TUTOR_SYSTEM_PROMPT は _get_tutor_system_prompt() に関数化されているはずです"


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


# ---------------------------------------------------------------------------
# 6. Issue #143: domain 引き継ぎ・動的プロンプト生成テスト
# ---------------------------------------------------------------------------


class TestDomainPropagation:
    """course_data の domain がシステムプロンプトに動的に反映されること。"""

    def test_integrated_tutor_prompt_uses_domain_from_course_data(self):
        """_get_integrated_tutor_system_prompt に渡した domain がプロンプトに含まれる。"""
        from api.routes.learning import _get_integrated_tutor_system_prompt

        prompt = _get_integrated_tutor_system_prompt("機械学習")
        assert "機械学習" in prompt
        assert "チューター" in prompt

    def test_tutor_prompt_uses_domain_from_course_data(self):
        """_get_tutor_system_prompt に渡した domain がプロンプトに含まれる。"""
        from api.routes.lecture import _get_tutor_system_prompt

        prompt = _get_tutor_system_prompt("経済学")
        assert "経済学" in prompt
        assert "チューター" in prompt

    def test_integrated_tutor_fallback_for_empty_domain(self):
        """domain が空文字（未設定）の場合にフォールバック文言が使われる。"""
        from api.routes.learning import _get_integrated_tutor_system_prompt

        prompt = _get_integrated_tutor_system_prompt("")
        assert "このコースの専門分野" in prompt

    def test_tutor_fallback_for_empty_domain(self):
        """domain が空文字の場合にフォールバック文言が使われる。"""
        from api.routes.lecture import _get_tutor_system_prompt

        prompt = _get_tutor_system_prompt("")
        assert "このコースの専門分野" in prompt

    def test_course_builder_prompt_contains_domain_field(self):
        """_COURSE_BUILDER_SYSTEM_PROMPT の JSON スキーマに domain フィールドが定義されている。"""
        from api.routes.admin import _COURSE_BUILDER_SYSTEM_PROMPT

        assert '"domain"' in _COURSE_BUILDER_SYSTEM_PROMPT
        assert "専門分野" in _COURSE_BUILDER_SYSTEM_PROMPT

    def test_course_builder_prompt_domain_instruction(self):
        """_COURSE_BUILDER_SYSTEM_PROMPT に domain の設定方法の指示が含まれる。"""
        from api.routes.admin import _COURSE_BUILDER_SYSTEM_PROMPT

        assert "domain" in _COURSE_BUILDER_SYSTEM_PROMPT
        # ナレッジグラフからの引き継ぎ指示
        assert "ナレッジグラフ" in _COURSE_BUILDER_SYSTEM_PROMPT
