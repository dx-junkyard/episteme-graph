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
import os
import re
from functools import lru_cache
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from core.config import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def get_embedding_dim() -> int:
    """現在の埋め込みベクトル次元数を返す (pgvector スキーマと一致する必要がある)。"""
    return int(get_settings().llm_embedding_dim)

# ---------------------------------------------------------------------------
# マルチモード LLM パラメータ取得
# ---------------------------------------------------------------------------

LLMMode = Literal["fast", "standard", "deep"]

_OPENAI_MODE_DEFAULTS = {
    "gpt-5.4-nano",
    "gpt-5.2",
}


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
    params = mode_map.get(mode, mode_map["fast"]).copy()

    # LLM_PROVIDER=google/gemini の環境で LLM_STANDARD_MODEL などが未注入だと、
    # OpenAI 向けデフォルトが Vertex/Gemini に渡り 404 になる。
    # その場合はプロバイダー共通の LLM_ANALYSIS_MODEL をフォールバックとして使う。
    if settings.llm_provider != "openai" and params["model"] in _OPENAI_MODE_DEFAULTS:
        params["model"] = settings.llm_analysis_model
        params["reasoning_effort"] = None

    return params


def max_tokens_for_model(model: str | None) -> int:
    """モデル名から対応するティアの最大出力トークン数を解決する。

    エージェント LLM 呼び出しは ``model=None`` で「Settings 既定の analysis モデル」
    を指す。明示モデルが各ティアのモデル名に一致すればそのティアの上限を返し、
    一致しなければ analysis ティアの上限をフォールバックとして返す。

    Parameters
    ----------
    model : str | None
        解決対象のモデル名。None は analysis ティア（generate_text の既定）。

    Returns
    -------
    int
        当該ティアの ``max_tokens`` 値。
    """
    settings = get_settings()
    name = (model or settings.llm_analysis_model or "").strip()
    # fast / analysis / standard / deep の順に照合する。analysis を standard /
    # deep より先に見るのは、model=None（= analysis モデル）が他ティアと同名でも
    # analysis ティアの上限に解決されるようにするため。
    for tier_model, tier_max in (
        (settings.llm_fast_model, settings.llm_fast_model_max_tokens),
        (settings.llm_analysis_model, settings.llm_analysis_model_max_tokens),
        (settings.llm_standard_model, settings.llm_standard_model_max_tokens),
        (settings.llm_deep_model, settings.llm_deep_model_max_tokens),
    ):
        if name and name == tier_model:
            return tier_max
    return settings.llm_analysis_model_max_tokens


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
# Gemini Adapter（内部実装）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_gemini_module():
    """google-generativeai モジュールを初期化して返す。"""
    import google.generativeai as genai  # type: ignore

    settings = get_settings()
    if not settings.llm_api_key:
        raise EnvironmentError(
            "LLM API キーが設定されていません。"
            " .env ファイルに LLM_API_KEY=... (または GEMINI_API_KEY=...) を追記してください。"
        )
    genai.configure(api_key=settings.llm_api_key)
    return genai


# ---------------------------------------------------------------------------
# Google Vertex AI Adapter（google-cloud-aiplatform / vertexai SDK）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_vertex_ai_client():
    """Vertex AI SDK (google-cloud-aiplatform) を初期化して返す。

    認証は以下の優先順で解決される:

    1. ``settings.google_application_credentials`` (= ``GOOGLE_APPLICATION_CREDENTIALS`` 環境変数)
       が設定されていればそのファイルを使用。Docker では ``/app/.gcp/credentials.json`` が既定。
    2. ``gcloud auth application-default login`` で生成された ADC クレデンシャル。
    3. GCE / Cloud Run 上のメタデータサーバー（サービスアカウント自動付与）。

    ``LLM_API_KEY`` / ``GEMINI_API_KEY`` は不要。
    """
    settings = get_settings()
    project = settings.gcp_project_id or settings.google_cloud_project or None
    location = settings.gcp_location or settings.google_cloud_location or "us-central1"

    # ① 認証ファイルパスを os.environ に明示セット（Google SDK が自動参照する）
    #    import より前に行うことでファイル不在を早期に検出できる。
    cred_path = settings.google_application_credentials
    if cred_path:
        if not os.path.isfile(cred_path):
            raise EnvironmentError(
                f"GOOGLE_APPLICATION_CREDENTIALS に指定されたファイルが見つかりません: {cred_path}\n"
                "  - .gcp/credentials.json が存在するか確認してください。\n"
                "  - docker-compose.yml の volumes で .gcp/ がマウントされているか確認してください。"
            )
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_path
        logger.debug("GOOGLE_APPLICATION_CREDENTIALS を設定しました: %s", cred_path)

    # ② SDK インポート & Vertex AI 初期化
    #    ADC は Google SDK が GOOGLE_APPLICATION_CREDENTIALS を自動参照する。
    import vertexai  # type: ignore[import]
    import google.auth.exceptions  # type: ignore[import]

    try:
        vertexai.init(project=project, location=location)
    except google.auth.exceptions.DefaultCredentialsError as exc:
        raise EnvironmentError(
            "Vertex AI ADC が見つかりません。以下のいずれかを設定してください:\n"
            "  1. .gcp/credentials.json にサービスアカウントキーを配置\n"
            "     (GOOGLE_APPLICATION_CREDENTIALS=/app/.gcp/credentials.json)\n"
            "  2. gcloud auth application-default login を実行し ADC を生成\n"
            "  3. GCP 環境（GCE / Cloud Run）上で実行する"
        ) from exc

    logger.info(
        "Vertex AI adapter initialized (project=%s, location=%s, creds=%s)",
        project or "<ADC default>",
        location,
        cred_path or "<ADC auto>",
    )
    import vertexai as _vtx  # noqa: F811
    return _vtx


def _extract_vertex_ai_text(response: Any) -> str:
    """Vertex AI response から text parts を結合して取り出す。"""
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        texts = [
            text
            for part in parts
            if (text := getattr(part, "text", None))
        ]
        if texts:
            return "".join(texts).strip()

    try:
        return (getattr(response, "text", "") or "").strip()
    except ValueError as exc:
        raise ValueError("Vertex AI response did not contain extractable text parts.") from exc


def _vertex_ai_generate_text(
    messages: list[dict[str, str]],
    model_name: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> str:
    from vertexai.generative_models import GenerativeModel, GenerationConfig  # type: ignore[import]

    system_instruction, contents = _messages_to_gemini(messages)
    config_kwargs: dict[str, Any] = {}
    if temperature is not None:
        config_kwargs["temperature"] = temperature
    if max_tokens is not None:
        config_kwargs["max_output_tokens"] = max_tokens
    generation_config = GenerationConfig(**config_kwargs) if config_kwargs else None

    model = GenerativeModel(
        model_name=model_name,
        system_instruction=system_instruction,
    )
    # Vertex AI の contents は {"role": ..., "parts": [...]} 形式でそのまま使用可
    kwargs: dict[str, Any] = {"generation_config": generation_config}
    if timeout is not None:
        kwargs["request_options"] = {"timeout": timeout}
    response = model.generate_content(contents, **kwargs)
    return _extract_vertex_ai_text(response)


def _vertex_ai_generate_structured(
    messages: list[dict[str, str]],
    response_format: type[T],
    model_name: str,
) -> T:
    from vertexai.generative_models import GenerativeModel, GenerationConfig  # type: ignore[import]

    system_instruction, contents = _messages_to_gemini(messages)
    schema = _sanitize_vertex_response_schema(response_format.model_json_schema())

    model = GenerativeModel(
        model_name=model_name,
        system_instruction=system_instruction,
    )
    response = model.generate_content(
        contents,
        generation_config=GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema,
        ),
    )
    raw = _extract_vertex_ai_text(response)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Vertex AI の構造化レスポンスを JSON として解析できませんでした: {raw!r}"
        ) from exc
    return response_format.model_validate(data)


def _sanitize_vertex_response_schema(schema: Any) -> Any:
    """Vertex AI response_schema が受け付けるJSON Schemaサブセットへ寄せる。

    Pydantic v2 は Optional[int] を ``anyOf: [{type: integer}, {type: null}]``
    のように出力するが、Vertex AI SDK の Schema enum には NULL がなく
    ParseError になる。nullable は「requiredに含めない」ことで表現し、
    null 型は response_schema から除去する。
    """
    definitions = schema.get("$defs", {}) if isinstance(schema, dict) else {}

    def clean(value: Any) -> Any:
        if isinstance(value, list):
            return [clean(item) for item in value]
        if not isinstance(value, dict):
            return value

        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.rsplit("/", 1)[-1]
            target = definitions.get(name)
            if isinstance(target, dict):
                return clean(target)

        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"$defs", "$ref", "default"}:
                continue
            if key == "anyOf" and isinstance(item, list):
                options = [
                    clean(option)
                    for option in item
                    if not (isinstance(option, dict) and str(option.get("type", "")).lower() == "null")
                ]
                if len(options) == 1 and isinstance(options[0], dict):
                    cleaned.update(options[0])
                elif options:
                    cleaned[key] = options
                continue
            cleaned[key] = clean(item)

        if str(cleaned.get("type", "")).lower() == "null":
            cleaned.pop("type", None)
        return cleaned

    return clean(schema)


def _vertex_ai_generate_embeddings(
    texts: list[str],
    model_name: str,
) -> list[list[float]]:
    from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput  # type: ignore[import]

    model = TextEmbeddingModel.from_pretrained(model_name)
    inputs = [TextEmbeddingInput(text=t) for t in texts]
    embeddings = model.get_embeddings(inputs)
    return [list(e.values) for e in embeddings]


# ---------------------------------------------------------------------------
# Gemini Vertex AI Adapter（廃止予定 / Deprecated）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_gemini_vertex_module():
    """Vertex AI ADC で初期化した google-generativeai モジュールを返す。

    .. deprecated::
        この認証方式は廃止予定です。
        ``LLM_PROVIDER=google`` への移行を推奨します。

    ADC の設定手順::

        gcloud auth application-default login

    ``GOOGLE_CLOUD_PROJECT`` と ``GOOGLE_CLOUD_LOCATION`` 環境変数も参照する。
    """
    import google.auth  # type: ignore[import]
    import google.auth.exceptions  # type: ignore[import]
    import google.generativeai as genai  # type: ignore[import]

    settings = get_settings()
    location = settings.google_cloud_location or "us-central1"

    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except google.auth.exceptions.DefaultCredentialsError as exc:
        raise EnvironmentError(
            "Vertex AI ADC が見つかりません。\n"
            "  gcloud auth application-default login\n"
            "を実行してから再起動してください。"
        ) from exc

    genai.configure(
        credentials=credentials,
        client_options={"api_endpoint": f"{location}-aiplatform.googleapis.com"},
    )
    logger.info(
        "Gemini Vertex AI adapter initialized (project=%s, location=%s) [DEPRECATED]",
        settings.google_cloud_project or "<ADC default>",
        location,
    )
    return genai


def _messages_to_gemini(
    messages: list[dict[str, str]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """OpenAI 形式メッセージを Gemini 形式 (system_instruction, contents) に変換する。

    - ``system`` → ``system_instruction`` に集約
    - ``user`` → ``role="user"``
    - ``assistant`` → ``role="model"``
    """
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        
        if role == "system":
            system_parts.append(content)
        elif role == "assistant":
            # Vertex AI SDK の厳格な仕様に合わせて {"text": content} とする
            contents.append({"role": "model", "parts": [{"text": content}]})
        else:
            contents.append({"role": "user", "parts": [{"text": content}]})
            
    system_instruction = "\n\n".join(system_parts) if system_parts else None
    return system_instruction, contents


def _gemini_generate_text(
    messages: list[dict[str, str]],
    model_name: str,
    *,
    genai_module: Any,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> str:
    genai = genai_module
    system_instruction, contents = _messages_to_gemini(messages)
    generation_config: dict[str, Any] = {}
    if temperature is not None:
        generation_config["temperature"] = temperature
    if max_tokens is not None:
        generation_config["max_output_tokens"] = max_tokens

    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system_instruction,
    )
    kwargs: dict[str, Any] = {"generation_config": generation_config or None}
    if timeout is not None:
        kwargs["request_options"] = {"timeout": timeout}
    response = model.generate_content(contents, **kwargs)
    return (getattr(response, "text", "") or "").strip()


def _gemini_generate_structured(
    messages: list[dict[str, str]],
    response_format: type[T],
    model_name: str,
    *,
    genai_module: Any,
) -> T:
    genai = genai_module
    system_instruction, contents = _messages_to_gemini(messages)
    schema = response_format.model_json_schema()
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=system_instruction,
    )
    response = model.generate_content(
        contents,
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": schema,
        },
    )
    raw = getattr(response, "text", "") or ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini の構造化レスポンスを JSON として解析できませんでした: {raw!r}") from exc
    return response_format.model_validate(data)


def _gemini_generate_embeddings(
    texts: list[str],
    model_name: str,
    *,
    genai_module: Any,
) -> list[list[float]]:
    genai = genai_module
    # Gemini の embed_content は text 単位のためループで呼び出す。
    result: list[list[float]] = []
    for t in texts:
        resp = genai.embed_content(model=model_name, content=t)
        # resp は dict-like: {"embedding": [...]} または {"embedding": {"values": [...]}}
        emb = resp["embedding"] if isinstance(resp, dict) else getattr(resp, "embedding", None)
        if isinstance(emb, dict) and "values" in emb:
            emb = emb["values"]
        result.append(list(emb or []))
    return result


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
    timeout: float | None = None,
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

    if settings.llm_provider == "gemini":
        return _gemini_generate_text(
            messages,
            model_name,
            genai_module=_get_gemini_module(),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    if settings.llm_provider == "google":
        _get_vertex_ai_client()  # ADC 初期化（キャッシュ済み）
        return _vertex_ai_generate_text(
            messages,
            model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    if settings.llm_provider == "gemini-vertex":  # 廃止予定
        return _gemini_generate_text(
            messages,
            model_name,
            genai_module=_get_gemini_vertex_module(),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

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
        timeout=timeout,
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

    if settings.llm_provider == "gemini":
        return _gemini_generate_structured(messages, response_format, model_name, genai_module=_get_gemini_module())

    if settings.llm_provider == "google":
        _get_vertex_ai_client()  # ADC 初期化（キャッシュ済み）
        return _vertex_ai_generate_structured(messages, response_format, model_name)

    if settings.llm_provider == "gemini-vertex":  # 廃止予定
        return _gemini_generate_structured(messages, response_format, model_name, genai_module=_get_gemini_vertex_module())

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

    if settings.llm_provider == "gemini":
        return _gemini_generate_embeddings(texts, model_name, genai_module=_get_gemini_module())

    if settings.llm_provider == "google":
        _get_vertex_ai_client()  # ADC 初期化（キャッシュ済み）
        return _vertex_ai_generate_embeddings(texts, model_name)

    if settings.llm_provider == "gemini-vertex":  # 廃止予定
        return _gemini_generate_embeddings(texts, model_name, genai_module=_get_gemini_vertex_module())

    client = _get_openai_client()

    resp = client.embeddings.create(model=model_name, input=texts)
    return [e.embedding for e in resp.data]


# ---------------------------------------------------------------------------
# 公開 API: 音声文字起こし (Speech-to-Text)
# ---------------------------------------------------------------------------

def transcribe_audio(
    audio_bytes: bytes,
    filename: str = "audio.webm",
    *,
    language: str = "ja",
    model: str | None = None,
) -> str:
    """音声データをテキストに文字起こしする（ハンズフリー会話用）。

    現状 openai プロバイダのみ対応（whisper-1 / gpt-4o-mini-transcribe 等、
    ``LLM_TRANSCRIBE_MODEL`` で切替）。他プロバイダでは RuntimeError を送出する
    （呼び出し側で 503 に変換すること）。

    Parameters
    ----------
    audio_bytes : bytes
        音声データ（webm / mp4 / wav 等、拡張子は filename から判定される）。
    filename : str
        フォーマット判定用のファイル名。
    language : str
        ISO-639-1 言語コード（言語指定のハードコード禁止ルールに従い引数で受ける）。

    Returns
    -------
    str
        文字起こし結果テキスト（空白のみの場合は空文字列）。
    """
    settings = get_settings()
    if settings.llm_provider != "openai":
        raise RuntimeError(
            f"transcribe_audio is not supported for provider '{settings.llm_provider}'"
        )
    model_name = model or settings.llm_transcribe_model
    client = _get_openai_client()
    import io

    buf = io.BytesIO(audio_bytes)
    buf.name = filename  # openai SDK はファイル名からフォーマットを判定する
    resp = client.audio.transcriptions.create(
        model=model_name,
        file=buf,
        language=language,
    )
    return (getattr(resp, "text", "") or "").strip()
