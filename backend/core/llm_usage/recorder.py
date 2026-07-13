"""record() + bounded in-memory buffer + flusher daemon thread（設計書 §3.3）。

U2（呼び出しを止めない・遅くしない）を構造的に守る:
  - record() は buffer への append のみを行い、DB I/O は一切しない（非ブロッキング）。
  - record() は例外を外に一切漏らさない（try/except で全体を包む）。
  - 実際の PostgreSQL への書き込みはバックグラウンドの daemon thread（flusher）が行う。
  - DB 不通時は buffer に残したままリトライする（buffer 上限が背圧になる）。

``_session_factory`` はテスト用の差し替えポイント（None なら ``core.postgres.get_session``
を遅延 import して使う）。
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from typing import Any, Callable

from sqlalchemy import text as sa_text

from core.config import get_settings
from core.llm_usage.schema import UsageEvent

logger = logging.getLogger(__name__)

# --- モジュール状態 ---
_lock = threading.Lock()
_flush_lock = threading.Lock()
_buffer: "deque[UsageEvent]" = deque()
_dropped = 0

_flusher_thread: threading.Thread | None = None
_stop_event = threading.Event()
_wake_event = threading.Event()

# テストが差し替えるポイント。None なら core.postgres.get_session を遅延 import する。
_session_factory: Callable[[], Any] | None = None

_INSERT_SQL = sa_text(
    """
    INSERT INTO llm_usage_events (
        provider, model, operation, feature, usage_source,
        prompt_tokens, completion_tokens, total_tokens, cached_tokens, reasoning_tokens,
        input_characters, output_characters, image_count, audio_bytes, tts_characters,
        duration_ms, success, error_type,
        user_id, document_id, course_id, run_id, metadata
    ) VALUES (
        :provider, :model, :operation, :feature, :usage_source,
        :prompt_tokens, :completion_tokens, :total_tokens, :cached_tokens, :reasoning_tokens,
        :input_characters, :output_characters, :image_count, :audio_bytes, :tts_characters,
        :duration_ms, :success, :error_type,
        CAST(:user_id AS uuid), CAST(:document_id AS uuid), :course_id, CAST(:run_id AS uuid),
        CAST(:metadata AS JSONB)
    )
    """
)


def _event_to_params(event: UsageEvent) -> dict:
    return {
        "provider": event.provider,
        "model": event.model,
        "operation": event.operation,
        "feature": event.feature,
        "usage_source": event.usage_source,
        "prompt_tokens": event.prompt_tokens,
        "completion_tokens": event.completion_tokens,
        "total_tokens": event.total_tokens,
        "cached_tokens": event.cached_tokens,
        "reasoning_tokens": event.reasoning_tokens,
        "input_characters": event.input_characters,
        "output_characters": event.output_characters,
        "image_count": event.image_count,
        "audio_bytes": event.audio_bytes,
        "tts_characters": event.tts_characters,
        "duration_ms": event.duration_ms,
        "success": event.success,
        "error_type": event.error_type,
        "user_id": event.user_id,
        "document_id": event.document_id,
        "course_id": event.course_id,
        "run_id": event.run_id,
        "metadata": json.dumps(event.metadata or {}, ensure_ascii=False, default=str),
    }


def record(event: UsageEvent) -> None:
    """UsageEvent をバッファに積む。いかなる場合も例外を外に漏らさない（U2）。

    ``settings.llm_usage_tracking_enabled=False`` の場合は no-op。
    バッファ上限（``llm_usage_buffer_max``）を超えた分は最古から捨て、
    ``dropped_count()`` に加算する（U8: 記録失敗も観測対象にする）。
    """
    global _dropped
    try:
        settings = get_settings()
        if not settings.llm_usage_tracking_enabled:
            return

        with _lock:
            _buffer.append(event)
            max_len = max(1, int(settings.llm_usage_buffer_max))
            while len(_buffer) > max_len:
                _buffer.popleft()
                _dropped += 1
            buffer_len = len(_buffer)

        _ensure_flusher_started()

        flush_batch = max(1, int(settings.llm_usage_flush_batch))
        if buffer_len >= flush_batch:
            _wake_event.set()
    except Exception:
        logger.debug("llm_usage.recorder.record failed", exc_info=True)


def dropped_count() -> int:
    """バッファ溢れで捨てたイベント数（U8）。"""
    with _lock:
        return _dropped


def _resolve_session():
    """DB セッションを取得する。取得自体に失敗した場合は None を返す（例外を出さない）。"""
    try:
        factory = _session_factory
        if factory is None:
            from core.postgres import get_session as factory  # 遅延 import
        return factory()
    except Exception:
        logger.debug("llm_usage.recorder: failed to obtain DB session", exc_info=True)
        return None


def flush_now() -> int:
    """バッファの内容を同期的に PostgreSQL へ書き込む。書けた行数を返す。

    DB エラー時は 0 を返し、バッファはそのまま保持する（次回リトライ）。
    """
    with _flush_lock:
        with _lock:
            if not _buffer:
                return 0
            batch = list(_buffer)

        session = _resolve_session()
        if session is None:
            return 0

        try:
            params = [_event_to_params(event) for event in batch]
            session.execute(_INSERT_SQL, params)
            session.commit()
        except Exception:
            logger.debug("llm_usage.recorder: flush failed, keeping buffer", exc_info=True)
            try:
                session.rollback()
            except Exception:
                pass
            return 0
        finally:
            try:
                session.close()
            except Exception:
                pass

        with _lock:
            for _ in range(min(len(batch), len(_buffer))):
                _buffer.popleft()

        return len(batch)


def _flusher_loop() -> None:
    while True:
        if _stop_event.is_set():
            return
        try:
            settings = get_settings()
            interval = max(0.1, float(settings.llm_usage_flush_interval_seconds))
        except Exception:
            interval = 10.0

        _wake_event.wait(interval)
        if _stop_event.is_set():
            return
        _wake_event.clear()

        try:
            flush_now()
        except Exception:
            logger.debug("llm_usage.recorder: flusher loop error", exc_info=True)


def _ensure_flusher_started() -> None:
    global _flusher_thread
    thread = _flusher_thread
    if thread is not None and thread.is_alive():
        return
    with _flush_lock:
        thread = _flusher_thread
        if thread is not None and thread.is_alive():
            return
        _stop_event.clear()
        new_thread = threading.Thread(
            target=_flusher_loop, name="llm-usage-flusher", daemon=True
        )
        _flusher_thread = new_thread
        new_thread.start()


def reset_for_tests() -> None:
    """バッファ・カウンタ・スレッド状態をリセットする（テスト用）。"""
    global _dropped, _flusher_thread

    _stop_event.set()
    _wake_event.set()
    thread = _flusher_thread
    if thread is not None:
        thread.join(timeout=2.0)

    with _lock:
        _buffer.clear()
        _dropped = 0

    _flusher_thread = None
    _stop_event.clear()
    _wake_event.clear()
