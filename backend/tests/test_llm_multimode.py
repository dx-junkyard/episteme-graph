"""Issue #57: LLMマルチモード設定および LangGraph ノードアーキテクチャのテスト。

- pydantic-settings による環境変数からの LLM 設定読み込み
- get_llm_params() が適切なモデルと推論レベルを返すこと
- StudentGraph の構造検証
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. マルチモード LLM 設定テスト
# ---------------------------------------------------------------------------


class TestLLMSettings:
    """pydantic-settings を用いたマルチモード LLM 設定のテスト。"""

    def test_default_fast_settings(self):
        """Fast モードのデフォルト値が正しいこと。"""
        from core.config import Settings

        s = Settings(
            _env_file=None,
            llm_api_key="sk-test",
        )
        assert s.llm_fast_model == "gpt-5.4-nano"
        assert s.llm_fast_effort == "low"

    def test_default_standard_settings(self):
        """Standard モードのデフォルト値が正しいこと。"""
        from core.config import Settings

        s = Settings(
            _env_file=None,
            llm_api_key="sk-test",
        )
        assert s.llm_standard_model == "gpt-5.2"
        assert s.llm_standard_effort == "medium"

    def test_default_deep_settings(self):
        """Deep モードのデフォルト値が正しいこと。"""
        from core.config import Settings

        s = Settings(
            _env_file=None,
            llm_api_key="sk-test",
        )
        assert s.llm_deep_model == "gpt-5.2"
        assert s.llm_deep_effort == "high"

    def test_env_override(self):
        """環境変数でモデル名と推論レベルを上書きできること。"""
        from core.config import Settings

        s = Settings(
            _env_file=None,
            llm_api_key="sk-test",
            llm_fast_model="gpt-4o-mini",
            llm_fast_effort="medium",
            llm_deep_effort="low",
        )
        assert s.llm_fast_model == "gpt-4o-mini"
        assert s.llm_fast_effort == "medium"
        assert s.llm_deep_effort == "low"

    def test_default_max_tokens_per_tier(self):
        """各ティアの max_tokens デフォルト（Fast=400k, 他=1M）が正しいこと。"""
        from core.config import Settings

        s = Settings(_env_file=None, llm_api_key="sk-test")
        assert s.llm_fast_model_max_tokens == 128_000
        assert s.llm_standard_model_max_tokens == 128_000
        assert s.llm_analysis_model_max_tokens == 128_000
        assert s.llm_deep_model_max_tokens == 128_000

    def test_max_tokens_env_override(self):
        """環境変数で各ティアの max_tokens を上書きできること。"""
        from core.config import Settings

        s = Settings(
            _env_file=None,
            llm_api_key="sk-test",
            llm_fast_model_max_tokens=128_000,
            llm_deep_model_max_tokens=200_000,
        )
        assert s.llm_fast_model_max_tokens == 128_000
        assert s.llm_deep_model_max_tokens == 200_000

    def test_effort_validation(self):
        """effort に不正な値を渡すとバリデーションエラーになること。"""
        from pydantic import ValidationError
        from core.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                llm_api_key="sk-test",
                llm_fast_effort="ultra",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# 2. get_llm_params() テスト
# ---------------------------------------------------------------------------


class TestGetLLMParams:
    """get_llm_params() が各モードに正しいパラメータを返すこと。"""

    @pytest.fixture(autouse=True)
    def _mock_settings(self):
        from core.config import Settings

        mock_settings = Settings(
            _env_file=None,
            llm_api_key="sk-test",
            llm_fast_model="gpt-5.4-nano",
            llm_fast_effort="low",
            llm_standard_model="gpt-5.2",
            llm_standard_effort="medium",
            llm_deep_model="gpt-5.2",
            llm_deep_effort="high",
        )
        with patch("core.llm.get_settings", return_value=mock_settings):
            yield

    def test_fast_mode(self):
        from core.llm import get_llm_params

        params = get_llm_params("fast")
        assert params["model"] == "gpt-5.4-nano"
        assert params["reasoning_effort"] == "low"

    def test_standard_mode(self):
        from core.llm import get_llm_params

        params = get_llm_params("standard")
        assert params["model"] == "gpt-5.2"
        assert params["reasoning_effort"] == "medium"

    def test_deep_mode(self):
        from core.llm import get_llm_params

        params = get_llm_params("deep")
        assert params["model"] == "gpt-5.2"
        assert params["reasoning_effort"] == "high"

    def test_invalid_mode_falls_back_to_fast(self):
        from core.llm import get_llm_params

        params = get_llm_params("unknown")  # type: ignore[arg-type]
        assert params["model"] == "gpt-5.4-nano"
        assert params["reasoning_effort"] == "low"


# ---------------------------------------------------------------------------
# 3. reasoning_effort が generate_text に渡されること
# ---------------------------------------------------------------------------


class TestReasoningEffortPassthrough:
    """generate_text に reasoning_effort を渡すと API kwargs に含まれること。"""

    def test_reasoning_effort_in_api_kwargs_for_reasoning_model(self):
        from core.llm import _build_api_kwargs

        kwargs = _build_api_kwargs("gpt-5.2", reasoning_effort="high")
        assert kwargs["reasoning_effort"] == "high"

    def test_reasoning_effort_ignored_for_non_reasoning_model(self):
        from core.llm import _build_api_kwargs

        kwargs = _build_api_kwargs("gpt-4o", reasoning_effort="high")
        assert "reasoning_effort" not in kwargs


# ---------------------------------------------------------------------------
# 4. StudentGraph 構造テスト
# ---------------------------------------------------------------------------


class TestStudentGraphStructure:
    """StudentGraph が正しいノードとエッジで構成されていること。"""

    @pytest.fixture()
    def graph(self):
        from core.graphs.student_graph import build_student_graph

        return build_student_graph()

    def test_graph_compiles(self, graph):
        """グラフがエラーなくコンパイルされること。"""
        assert graph is not None

    def test_student_graph_no_chunks_bypass(self):
        """チャンク0件の場合に LLM をスキップしてエラー応答を返すこと。"""
        from core.graphs.student_graph import no_chunks_response_node

        result = no_chunks_response_node({"question": "テスト"})
        assert "answer" in result
        assert "教材" in result["answer"]
        assert result.get("error") == "no_relevant_chunks"

    def test_query_analyzer_fallback(self):
        """QueryAnalyzer の LLM 呼び出しが失敗しても安全にフォールバックすること。"""
        from core.graphs.student_graph import query_analyzer_node

        with patch("core.graphs.student_graph.generate_text", side_effect=Exception("LLM error")):
            result = query_analyzer_node({"question": "量子力学とは"})
            assert result["intent"] == "other"
            assert result["search_keywords"] == ["量子力学とは"]

    def test_retrieval_no_chunks(self):
        """search_chunks_with_metadata が空を返した場合に no_relevant_chunks=True。"""
        import sys
        mock_services = MagicMock()
        mock_services.search_chunks_with_metadata = MagicMock(return_value=[])
        original_services = sys.modules.get("services")
        sys.modules["services"] = mock_services
        try:
            from core.graphs.student_graph import retrieval_node
            result = retrieval_node({"question": "テスト"})
            assert result["no_relevant_chunks"] is True
            assert result["chunks"] == []
        finally:
            if original_services is None:
                sys.modules.pop("services", None)
            else:
                sys.modules["services"] = original_services

    def test_retrieval_with_chunks(self):
        """閾値以上のチャンクがある場合に no_relevant_chunks=False。"""
        import sys
        mock_chunks = [
            {"text": "チャンク1", "source_title": "教材A", "source_file": "a.pdf", "score": 0.8},
            {"text": "チャンク2", "source_title": "教材B", "source_file": "b.pdf", "score": 0.2},
        ]
        mock_services = MagicMock()
        mock_services.search_chunks_with_metadata = MagicMock(return_value=mock_chunks)
        original_services = sys.modules.get("services")
        sys.modules["services"] = mock_services
        try:
            from core.graphs.student_graph import retrieval_node
            result = retrieval_node({"question": "テスト"})
            assert result["no_relevant_chunks"] is False
            assert len(result["chunks"]) == 1  # score >= 0.35 のみ
        finally:
            if original_services is None:
                sys.modules.pop("services", None)
            else:
                sys.modules["services"] = original_services

    def test_format_guard_fallback(self):
        """FormatGuard の LLM 呼び出しが失敗しても raw_answer をそのまま返すこと。"""
        from core.graphs.student_graph import format_guard_node

        with patch("core.graphs.student_graph.generate_text", side_effect=Exception("LLM error")):
            result = format_guard_node({"raw_answer": "テスト回答"})
            assert result["answer"] == "テスト回答"
