"""LLM 抽象化レイヤー (Adapter) for Episteme Graph.

サービス層（extractor / chat / batch 等）は本モジュールの公開関数のみを
使用し、``import openai`` や ``import google.generativeai`` を直接行わない。

公開 API
--------
- ``generate_text(messages, ...)``   — チャット補完
- ``generate_text_with_structured_output(messages, response_format, ...)``
                                     — 構造化出力（Pydantic モデル）
- ``generate_embeddings(texts)``     — テキスト埋め込み

内部では ``core.config.Settings`` からモデル名を取得し、
Reasoning モデル（o1, o3-mini, gpt-5.x 等）向けの自動変換を行う:

- ``system`` ロール → ``developer`` ロールへの変換
- ``temperature`` / ``max_tokens`` の自動除去
- 将来的な Gemini / Claude 対応のための拡張ポイント
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from core.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# マルチモード LLM パラメータ取得
# ---------------------------------------------------------------------------

LLMMode = Literal["fast", "standard", "deep"]


def get_llm_params(mode: LLMMode) -> dict[str, Any]:
    """指定モードに応じた LLM パラメータ (model, reasoning_effort) を返す。

    Parameters
    ----------
    mode : ``"fast"`` | ``"standard"`` | ``"deep"``

    Returns
    -------
    dict
        ``{"model": str, "reasoning_effort": str}``
    """
    settings = get_settings()
    mode_map: dict[str, dict[str, str]] = {
        "fast": {
            "model": settings.llm_fast_model,
            "reasoning_effort": settings.llm_fast_effort,
        },
        "standard": {
            "model": settings.llm_standard_model,
            "reasoning_effort": settings.llm_standard_effort,
        },
        "deep": {
            "model": settings.llm_deep_model,
            "reasoning_effort": settings.llm_deep_effort,
        },
    }
    return mode_map.get(mode, mode_map["fast"])


# ---------------------------------------------------------------------------
# Reasoning モデル判定
# ---------------------------------------------------------------------------

_REASONING_MODEL_PATTERNS = re.compile(
    r"^(o1|o3|o4|gpt-5)"
)


def _is_reasoning_model(model_name: str) -> bool:
    """モデル名が Reasoning モデルかどうかを判定する。"""
    return bool(_REASONING_MODEL_PATTERNS.match(model_name))


# ---------------------------------------------------------------------------
# メッセージ前処理（Reasoning モデル対応）
# ---------------------------------------------------------------------------

def _adapt_messages_for_model(
    messages: list[dict[str, str]],
    model_name: str,
) -> list[dict[str, str]]:
    """Reasoning モデル向けにメッセージリストを変換する。

    - ``system`` ロールを ``developer`` ロールに変換
    - 非 Reasoning モデルの場合はそのまま返す
    """
    if not _is_reasoning_model(model_name):
        return messages

    adapted: list[dict[str, str]] = []
    for msg in messages:
        if msg.get("role") == "system":
            adapted.append({"role": "developer", "content": msg["content"]})
        else:
            adapted.append(msg)
    return adapted


def _build_api_kwargs(
    model_name: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
    extra_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reasoning モデルでは temperature / max_tokens を除去し、
    代わりに max_completion_tokens を使う。
    """
    kwargs: dict[str, Any] = {}

    if _is_reasoning_model(model_name):
        # Reasoning モデル: temperature 禁止, max_tokens → max_completion_tokens
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        # reasoning_effort が指定されていれば付与
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort
    else:
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

    if extra_kwargs:
        kwargs.update(extra_kwargs)

    return kwargs


# ---------------------------------------------------------------------------
# OpenAI Adapter（内部実装）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_openai_client():
    """OpenAI クライアントのシングルトンを返す。"""
    from openai import OpenAI  # ベンダ依存を本モジュール内に封じ込める

    settings = get_settings()
    if not settings.llm_api_key or settings.llm_api_key.startswith("sk-your"):
        raise EnvironmentError(
            "LLM API キーが設定されていません。"
            " .env ファイルに LLM_API_KEY=sk-... (または OPENAI_API_KEY=sk-...) を追記してください。"
        )
    return OpenAI(api_key=settings.llm_api_key)


# ---------------------------------------------------------------------------
# 公開 API: テキスト生成
# ---------------------------------------------------------------------------

def generate_text(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """チャット補完でテキストを生成する。

    Parameters
    ----------
    messages : list[dict]
        ``[{"role": "system"|"user"|"assistant", "content": "..."}]`` 形式。
        Reasoning モデルの場合は ``system`` → ``developer`` に自動変換される。
    model : str | None
        使用するモデル名。None の場合は Settings の analysis_model を使用。
    temperature : float | None
        Reasoning モデルでは自動的に除去される。
    max_tokens : int | None
        Reasoning モデルでは max_completion_tokens に変換される。
    reasoning_effort : str | None
        Reasoning モデルの推論レベル (``"low"`` / ``"medium"`` / ``"high"``)。

    Returns
    -------
    str
        LLM のレスポンステキスト。
    """
    settings = get_settings()
    model_name = model or settings.llm_analysis_model
    client = _get_openai_client()

    adapted_messages = _adapt_messages_for_model(messages, model_name)
    api_kwargs = _build_api_kwargs(
        model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
    )

    response = client.chat.completions.create(
        model=model_name,
        messages=adapted_messages,
        **api_kwargs,
    )
    return response.choices[0].message.content or ""


def generate_text_with_structured_output(
    messages: list[dict[str, str]],
    response_format: type[T],
    *,
    model: str | None = None,
) -> T:
    """構造化出力（Pydantic モデル）でチャット補完を行う。

    OpenAI の ``beta.chat.completions.parse`` を使用。

    Parameters
    ----------
    messages : list[dict]
        メッセージリスト。
    response_format : type[T]
        出力に期待する Pydantic モデルクラス。
    model : str | None
        使用するモデル名。

    Returns
    -------
    T
        パースされた Pydantic モデルインスタンス。
    """
    settings = get_settings()
    model_name = model or settings.llm_analysis_model
    client = _get_openai_client()

    adapted_messages = _adapt_messages_for_model(messages, model_name)

    response = client.beta.chat.completions.parse(
        model=model_name,
        messages=adapted_messages,
        response_format=response_format,
    )
    return response.choices[0].message.parsed


# ---------------------------------------------------------------------------
# 公開 API: Embeddings
# ---------------------------------------------------------------------------

def generate_embeddings(
    texts: list[str],
    *,
    model: str | None = None,
) -> list[list[float]]:
    """テキストリストを Embedding ベクトルに変換する。

    Parameters
    ----------
    texts : list[str]
        埋め込み対象のテキストリスト。
    model : str | None
        使用する Embedding モデル名。None の場合は Settings の embedding_model を使用。

    Returns
    -------
    list[list[float]]
        各テキストに対応する Embedding ベクトルのリスト。
    """
    if not texts:
        return []

    settings = get_settings()
    model_name = model or settings.llm_embedding_model
    client = _get_openai_client()

    resp = client.embeddings.create(model=model_name, input=texts)
    return [e.embedding for e in resp.data]
