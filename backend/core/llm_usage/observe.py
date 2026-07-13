"""core/llm.py（+ core/tts.py）からのフック地点（設計書 §3.2）。

実測 usage 抽出（reported）→ 取れなければ estimator でフォールバック（estimated_*）
→ UsageEvent 組立 → recorder.record()。

全関数とも例外を外に絶対に漏らさない（U2）。呼び出し元の LLM 呼び出しフローを
壊してはならないため、内部で何が起きても最悪ログに残すだけに留める。
"""

from __future__ import annotations

import json
import logging
import time

from core.llm_usage import estimator
from core.llm_usage.context import current_usage_context
from core.llm_usage.estimator import _extract_text_from_content
from core.llm_usage.recorder import record
from core.llm_usage.schema import UsageEvent

logger = logging.getLogger(__name__)


def _duration_ms(started_monotonic: float | None) -> int | None:
    if started_monotonic is None:
        return None
    try:
        return int((time.monotonic() - started_monotonic) * 1000)
    except Exception:
        return None


def _safe_chain_getattr(obj, *names):
    """ネストした getattr を安全に辿る。途中で None なら None を返す（例外は出さない）。"""
    current = obj
    for name in names:
        if current is None:
            return None
        try:
            current = getattr(current, name, None)
        except Exception:
            return None
    return current


def _extract_openai_usage(response) -> dict | None:
    usage = _safe_chain_getattr(response, "usage")
    if usage is None:
        return None
    prompt_tokens = _safe_chain_getattr(usage, "prompt_tokens")
    completion_tokens = _safe_chain_getattr(usage, "completion_tokens")
    total_tokens = _safe_chain_getattr(usage, "total_tokens")
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    cached_tokens = _safe_chain_getattr(usage, "prompt_tokens_details", "cached_tokens")
    reasoning_tokens = _safe_chain_getattr(usage, "completion_tokens_details", "reasoning_tokens")
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def _extract_gemini_usage(response) -> dict | None:
    usage_metadata = _safe_chain_getattr(response, "usage_metadata")
    if usage_metadata is None:
        return None
    prompt_tokens = _safe_chain_getattr(usage_metadata, "prompt_token_count")
    completion_tokens = _safe_chain_getattr(usage_metadata, "candidates_token_count")
    total_tokens = _safe_chain_getattr(usage_metadata, "total_token_count")
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": None,
        "reasoning_tokens": None,
    }


def _extract_reported_usage(response) -> dict | None:
    """OpenAI 形・Gemini/Vertex 形どちらの response からも実測 usage を試みる。"""
    if response is None:
        return None
    return _extract_openai_usage(response) or _extract_gemini_usage(response)


def _extract_embedding_usage(response) -> dict | None:
    usage = _safe_chain_getattr(response, "usage")
    if usage is None:
        return None
    prompt_tokens = _safe_chain_getattr(usage, "prompt_tokens")
    if prompt_tokens is None:
        return None
    return {"prompt_tokens": prompt_tokens}


def _messages_char_count(messages: list[dict] | None) -> int:
    total = 0
    for message in messages or []:
        content = message.get("content", "") if isinstance(message, dict) else ""
        total += len(_extract_text_from_content(content))
    return total


def _emit(event: UsageEvent) -> None:
    # recorder.record() 自体も例外を漏らさない実装だが、二重に握りつぶして
    # observe.py 単体としても U2 を満たす。
    try:
        record(event)
    except Exception:
        logger.debug("llm_usage.observe: record() failed", exc_info=True)


def observe_chat(
    *,
    provider: str,
    model: str,
    messages: list[dict],
    operation: str = "chat",
    response=None,
    response_text: str | None = None,
    schema: dict | None = None,
    error: BaseException | None = None,
    started_monotonic: float | None = None,
) -> None:
    """chat / structured output 呼び出しの使用量を記録する。"""
    try:
        ctx = current_usage_context()
        duration_ms = _duration_ms(started_monotonic)
        input_characters = _messages_char_count(messages)

        if error is not None:
            input_estimate = estimator.estimate_messages_tokens(messages, model=model, schema=schema)
            _emit(
                UsageEvent(
                    provider=provider,
                    model=model,
                    operation=operation,
                    feature=ctx.feature,
                    usage_source=input_estimate.usage_source,
                    prompt_tokens=input_estimate.tokens,
                    total_tokens=input_estimate.tokens,
                    input_characters=input_characters,
                    duration_ms=duration_ms,
                    success=False,
                    error_type=type(error).__name__,
                    user_id=ctx.user_id,
                    document_id=ctx.document_id,
                    course_id=ctx.course_id,
                    run_id=ctx.run_id,
                )
            )
            return

        reported = _extract_reported_usage(response)
        if reported is not None:
            _emit(
                UsageEvent(
                    provider=provider,
                    model=model,
                    operation=operation,
                    feature=ctx.feature,
                    usage_source="reported",
                    prompt_tokens=reported.get("prompt_tokens"),
                    completion_tokens=reported.get("completion_tokens"),
                    total_tokens=reported.get("total_tokens"),
                    cached_tokens=reported.get("cached_tokens"),
                    reasoning_tokens=reported.get("reasoning_tokens"),
                    input_characters=input_characters,
                    output_characters=len(response_text) if response_text else None,
                    duration_ms=duration_ms,
                    success=True,
                    user_id=ctx.user_id,
                    document_id=ctx.document_id,
                    course_id=ctx.course_id,
                    run_id=ctx.run_id,
                )
            )
            return

        # usage が取れない場合のフォールバック推計
        input_estimate = estimator.estimate_messages_tokens(messages, model=model, schema=schema)
        output_tokens = None
        metadata: dict = {}
        if response_text:
            output_estimate = estimator.estimate_text_tokens(response_text, model=model)
            output_tokens = output_estimate.tokens
            # reasoning モデルの不可視 reasoning トークンは推計不能。過小になりうることを正直に記録する。
            metadata["reasoning_excluded"] = True

        total_tokens = input_estimate.tokens + (output_tokens or 0)
        _emit(
            UsageEvent(
                provider=provider,
                model=model,
                operation=operation,
                feature=ctx.feature,
                usage_source=input_estimate.usage_source,
                prompt_tokens=input_estimate.tokens,
                completion_tokens=output_tokens,
                total_tokens=total_tokens,
                input_characters=input_characters,
                output_characters=len(response_text) if response_text else None,
                duration_ms=duration_ms,
                success=True,
                user_id=ctx.user_id,
                document_id=ctx.document_id,
                course_id=ctx.course_id,
                run_id=ctx.run_id,
                metadata=metadata,
            )
        )
    except Exception:
        logger.debug("llm_usage.observe_chat failed", exc_info=True)


def _estimate_texts_tokens(texts: list[str] | None, model: str | None):
    combined = "\n".join(t for t in (texts or []) if isinstance(t, str))
    return estimator.estimate_text_tokens(combined, model=model)


def observe_embeddings(
    *,
    provider: str,
    model: str,
    texts: list[str],
    response=None,
    error: BaseException | None = None,
    started_monotonic: float | None = None,
) -> None:
    """embeddings 呼び出しの使用量を記録する（出力トークンなし）。"""
    try:
        ctx = current_usage_context()
        duration_ms = _duration_ms(started_monotonic)
        input_characters = sum(len(t) for t in (texts or []) if isinstance(t, str))

        if error is not None:
            estimate = _estimate_texts_tokens(texts, model)
            _emit(
                UsageEvent(
                    provider=provider,
                    model=model,
                    operation="embedding",
                    feature=ctx.feature,
                    usage_source=estimate.usage_source,
                    prompt_tokens=estimate.tokens,
                    total_tokens=estimate.tokens,
                    input_characters=input_characters,
                    duration_ms=duration_ms,
                    success=False,
                    error_type=type(error).__name__,
                    user_id=ctx.user_id,
                    document_id=ctx.document_id,
                    course_id=ctx.course_id,
                    run_id=ctx.run_id,
                )
            )
            return

        reported = _extract_embedding_usage(response)
        if reported is not None:
            prompt_tokens = reported.get("prompt_tokens")
            _emit(
                UsageEvent(
                    provider=provider,
                    model=model,
                    operation="embedding",
                    feature=ctx.feature,
                    usage_source="reported",
                    prompt_tokens=prompt_tokens,
                    total_tokens=prompt_tokens,
                    input_characters=input_characters,
                    duration_ms=duration_ms,
                    success=True,
                    user_id=ctx.user_id,
                    document_id=ctx.document_id,
                    course_id=ctx.course_id,
                    run_id=ctx.run_id,
                )
            )
            return

        estimate = _estimate_texts_tokens(texts, model)
        _emit(
            UsageEvent(
                provider=provider,
                model=model,
                operation="embedding",
                feature=ctx.feature,
                usage_source=estimate.usage_source,
                prompt_tokens=estimate.tokens,
                total_tokens=estimate.tokens,
                input_characters=input_characters,
                duration_ms=duration_ms,
                success=True,
                user_id=ctx.user_id,
                document_id=ctx.document_id,
                course_id=ctx.course_id,
                run_id=ctx.run_id,
            )
        )
    except Exception:
        logger.debug("llm_usage.observe_embeddings failed", exc_info=True)


def observe_vision(
    *,
    provider: str,
    model: str,
    prompt: str,
    image_count: int,
    image_dims: list[tuple[int, int] | None] | None = None,
    schema: dict | None = None,
    response=None,
    error: BaseException | None = None,
    started_monotonic: float | None = None,
) -> None:
    """vision（画像付き）呼び出しの使用量を記録する。"""
    try:
        ctx = current_usage_context()
        duration_ms = _duration_ms(started_monotonic)
        input_characters = len(prompt) if prompt else None
        dims_list = image_dims if image_dims is not None else [None] * max(0, image_count)

        metadata: dict = {}
        if any(dims is None for dims in dims_list):
            # 寸法不明 → 定数フォールバックを使ったことを出所として残す（設計書 §4.3）
            metadata["image_estimate"] = "flat"

        if error is not None:
            text_estimate = estimator.estimate_text_tokens(prompt or "", model=model)
            vision_estimate = estimator.estimate_vision_tokens(dims_list)
            total = text_estimate.tokens + vision_estimate.tokens
            _emit(
                UsageEvent(
                    provider=provider,
                    model=model,
                    operation="vision",
                    feature=ctx.feature,
                    usage_source=text_estimate.usage_source,
                    prompt_tokens=total,
                    total_tokens=total,
                    input_characters=input_characters,
                    image_count=image_count,
                    duration_ms=duration_ms,
                    success=False,
                    error_type=type(error).__name__,
                    user_id=ctx.user_id,
                    document_id=ctx.document_id,
                    course_id=ctx.course_id,
                    run_id=ctx.run_id,
                    metadata=metadata,
                )
            )
            return

        reported = _extract_reported_usage(response)
        if reported is not None:
            _emit(
                UsageEvent(
                    provider=provider,
                    model=model,
                    operation="vision",
                    feature=ctx.feature,
                    usage_source="reported",
                    prompt_tokens=reported.get("prompt_tokens"),
                    completion_tokens=reported.get("completion_tokens"),
                    total_tokens=reported.get("total_tokens"),
                    cached_tokens=reported.get("cached_tokens"),
                    reasoning_tokens=reported.get("reasoning_tokens"),
                    input_characters=input_characters,
                    image_count=image_count,
                    duration_ms=duration_ms,
                    success=True,
                    user_id=ctx.user_id,
                    document_id=ctx.document_id,
                    course_id=ctx.course_id,
                    run_id=ctx.run_id,
                    metadata=metadata,
                )
            )
            return

        text_estimate = estimator.estimate_text_tokens(prompt or "", model=model)
        schema_tokens = 0
        if schema:
            schema_estimate = estimator.estimate_text_tokens(
                json.dumps(schema, ensure_ascii=False), model=model
            )
            schema_tokens = schema_estimate.tokens
        vision_estimate = estimator.estimate_vision_tokens(dims_list)
        total_input = text_estimate.tokens + vision_estimate.tokens + schema_tokens

        _emit(
            UsageEvent(
                provider=provider,
                model=model,
                operation="vision",
                feature=ctx.feature,
                usage_source=text_estimate.usage_source,
                prompt_tokens=total_input,
                total_tokens=total_input,
                input_characters=input_characters,
                image_count=image_count,
                duration_ms=duration_ms,
                success=True,
                user_id=ctx.user_id,
                document_id=ctx.document_id,
                course_id=ctx.course_id,
                run_id=ctx.run_id,
                metadata=metadata,
            )
        )
    except Exception:
        logger.debug("llm_usage.observe_vision failed", exc_info=True)


def observe_transcribe(
    *,
    provider: str,
    model: str,
    audio_bytes_len: int,
    error: BaseException | None = None,
    started_monotonic: float | None = None,
) -> None:
    """音声文字起こし（STT）呼び出しの使用量を記録する。

    トークン概念が無い操作のため、既知の正確値である audio_bytes を記録する
    （usage_source='reported' — 推計ではなく、呼び出し前から分かっている実測値の意）。
    """
    try:
        ctx = current_usage_context()
        duration_ms = _duration_ms(started_monotonic)
        _emit(
            UsageEvent(
                provider=provider,
                model=model,
                operation="transcribe",
                feature=ctx.feature,
                usage_source="reported",
                audio_bytes=audio_bytes_len,
                duration_ms=duration_ms,
                success=error is None,
                error_type=type(error).__name__ if error is not None else None,
                user_id=ctx.user_id,
                document_id=ctx.document_id,
                course_id=ctx.course_id,
                run_id=ctx.run_id,
            )
        )
    except Exception:
        logger.debug("llm_usage.observe_transcribe failed", exc_info=True)


def observe_tts(
    *,
    provider: str,
    model: str,
    characters: int,
    error: BaseException | None = None,
    started_monotonic: float | None = None,
) -> None:
    """TTS 呼び出しの使用量を記録する（文字課金のため文字数を正とする）。"""
    try:
        ctx = current_usage_context()
        duration_ms = _duration_ms(started_monotonic)
        _emit(
            UsageEvent(
                provider=provider,
                model=model,
                operation="tts",
                feature=ctx.feature,
                usage_source="reported",
                tts_characters=characters,
                duration_ms=duration_ms,
                success=error is None,
                error_type=type(error).__name__ if error is not None else None,
                user_id=ctx.user_id,
                document_id=ctx.document_id,
                course_id=ctx.course_id,
                run_id=ctx.run_id,
            )
        )
    except Exception:
        logger.debug("llm_usage.observe_tts failed", exc_info=True)
