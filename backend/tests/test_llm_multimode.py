"""Issue #57: LLMマルチモード設定のテスト。

- pydantic-settings による環境変数からの LLM 設定読み込み
- get_llm_params() が適切なモデルと推論レベルを返すこと

（旧 StudentGraph（LangGraph）の構造検証は、本番ルートへ未接続のまま残っていた
``core/graphs/`` の撤去に伴って削除した。学生向け対話の本番実装は
``api/routes/learning.py::learning_chat``。）
"""

from __future__ import annotations

from unittest.mock import patch

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
