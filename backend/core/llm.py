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

import json
import logging
import re
from functools import lru_cache
from typing import Any, TypeVar

from pydantic import BaseModel

from core.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# Reasoning モデル判定
# ---------------------------------------------------------------------------

_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4")

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
    if not settings.openai_api_key or settings.openai_api_key.startswith("sk-your"):
        raise EnvironmentError(
            "OPENAI_API_KEY が設定されていません。"
            " .env ファイルに OPENAI_API_KEY=sk-... を追記してください。"
        )
    return OpenAI(api_key=settings.openai_api_key)


# ---------------------------------------------------------------------------
# 公開 API: テキスト生成
# ---------------------------------------------------------------------------

def generate_text(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
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

    Returns
    -------
    str
        LLM のレスポンステキスト。
    """
    settings = get_settings()
    model_name = model or settings.openai_analysis_model
    client = _get_openai_client()

    adapted_messages = _adapt_messages_for_model(messages, model_name)
    api_kwargs = _build_api_kwargs(
        model_name,
        temperature=temperature,
        max_tokens=max_tokens,
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
    model_name = model or settings.openai_analysis_model
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
    model_name = model or settings.openai_embedding_model
    client = _get_openai_client()

    resp = client.embeddings.create(model=model_name, input=texts)
    return [e.embedding for e in resp.data]


# ---------------------------------------------------------------------------
# 後方互換: 旧 API（段階的に廃止予定）
# ---------------------------------------------------------------------------

def get_client():
    """OpenAI クライアントを返す（後方互換）。

    .. deprecated::
        ``generate_text()`` / ``generate_embeddings()`` を使用してください。
    """
    return _get_openai_client()


class _LegacySettings:
    """旧 LLMSettings の後方互換ラッパー。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def api_key(self) -> str:
        return self._settings.openai_api_key

    @property
    def analysis_model(self) -> str:
        return self._settings.openai_analysis_model

    @property
    def embedding_model(self) -> str:
        return self._settings.openai_embedding_model


@lru_cache(maxsize=1)
def get_settings_legacy() -> _LegacySettings:
    """旧 get_settings() の後方互換。

    .. deprecated::
        ``core.config.get_settings()`` を使用してください。
    """
    return _LegacySettings(get_settings())


# ---------------------------------------------------------------------------
# Missing Link Suggestion — LLM prompt engineering
# ---------------------------------------------------------------------------

def generate_missing_link_suggestions(
    pattern_name: str,
    pattern_description: str,
    structural_rules: list[str],
    variables_template: list[str],
    existing_fields: list[str] | None = None,
) -> dict:
    """パターンメタデータを受け取り、構造的空白を検知して分野横断の検索クエリを生成する。

    Returns a dict matching the MissingLinkSuggestion schema (without pattern_id).
    """
    rules_text = "\n".join(f"  - {r}" for r in structural_rules) if structural_rules else "  (none)"
    vars_text = ", ".join(variables_template) if variables_template else "(none)"
    existing_text = ", ".join(existing_fields) if existing_fields else "none known"

    prompt = f"""You are a cross-domain research advisor for the Episteme Graph system.

Given the following abstraction pattern, suggest academic fields where this structural pattern
likely occurs but is NOT yet represented in our pattern library.

## Pattern Information
- **Name**: {pattern_name}
- **Description**: {pattern_description}
- **Abstract Variables**: {vars_text}
- **Structural Rules**:
{rules_text}
- **Fields already covered**: {existing_text}

## Your Task
1. Identify 3-5 academic fields/domains where this same structural pattern likely manifests,
   but which are NOT in the "already covered" list.
2. For each field, explain WHY this pattern would appear there (concrete reasoning, not generic).
3. For each field, provide 2-4 arXiv search keywords that combine the pattern's structural
   concepts with field-specific terminology. Keywords should be specific enough to find relevant
   papers, mixing both generic structural terms and specialized domain terms.

## Output Format (strict JSON)
Return ONLY a JSON object with this structure:
{{
  "suggestions": [
    {{
      "field": "<academic field name>",
      "reasoning": "<1-2 sentences explaining why this pattern appears in this field>",
      "keywords": ["<keyword1>", "<keyword2>", "<keyword3>"]
    }}
  ]
}}

Important:
- Do NOT include fields already covered.
- Keywords must be suitable for arXiv search (English, technical terms).
- Balance generic structural terms with field-specific jargon to mitigate hallucination."""

    raw = generate_text(
        messages=[{"role": "user", "content": prompt}],
    )

    # Strip markdown code fences if present
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    return json.loads(cleaned)
