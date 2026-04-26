"""Admin error log analysis endpoints.

直近のエラーをメモリ上のリングバッファに保持し、Admin UI から検索できるようにする。
"""

from __future__ import annotations

import contextvars
import datetime
import logging
import re
import traceback
from collections import deque
from typing import Any

import jwt
from fastapi import APIRouter, Depends, Request

from dependencies import _JWT_ALGORITHM, _require_system_admin
from core.config import get_settings as _get_settings

router = APIRouter(prefix="/api/admin/error-logs", tags=["Admin Error Logs"])

_MAX_LOGS = max(1, _get_settings().admin_error_log_max_items)
_LOG_BUFFER: deque[dict[str, Any]] = deque(maxlen=_MAX_LOGS)
_HANDLER_INSTALLED = False
_request_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "error_log_request_context",
    default={},
)

_ID_KEYS = ("session_id", "user_id", "material_id", "course_id")
_KEY_PATTERNS = {
    "session_id": re.compile(r"(?:session[_ -]?id|session)\s*[=:]\s*([A-Za-z0-9._:-]+)", re.I),
    "user_id": re.compile(r"(?:user[_ -]?id|user)\s*[=:]\s*([A-Za-z0-9._:-]+)", re.I),
    "material_id": re.compile(r"(?:material[_ -]?id|material|doc(?:ument)?[_ -]?id)\s*[=:]\s*([A-Za-z0-9._:-]+)", re.I),
    "course_id": re.compile(r"(?:course[_ -]?id|course)\s*[=:]\s*([A-Za-z0-9._:-]+)", re.I),
}
_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PATTERN = (
    r"authorization|set-cookie|cookie|password|passwd|passphrase|passcode|"
    r"access[_-]?token|refresh[_-]?token|id[_-]?token|token|jwt|"
    r"api[_-]?key|secret|client[_-]?secret|openai[_-]?api[_-]?key|"
    r"gemini[_-]?api[_-]?key|google[_-]?api[_-]?key|llm[_-]?api[_-]?key"
)
_BARE_SENSITIVE_KEY_PATTERN = (
    r"password|passwd|passphrase|passcode|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|token|jwt|api[_-]?key|secret|client[_-]?secret|"
    r"openai[_-]?api[_-]?key|gemini[_-]?api[_-]?key|"
    r"google[_-]?api[_-]?key|llm[_-]?api[_-]?key"
)
_QUOTED_SECRET_RE = re.compile(
    rf"(?P<prefix>(?P<key_quote>['\"]?)(?:{_SENSITIVE_KEY_PATTERN})(?P=key_quote)\s*:\s*)"
    r"(?P<value_quote>['\"])(?P<value>.*?)(?P=value_quote)",
    re.I | re.S,
)
_AUTH_HEADER_RE = re.compile(
    rf"(?P<prefix>\b(?:authorization)\b\s*[:=]\s*(?:bearer|basic)\s+)"
    r"(?P<value>[^\s,;]+)",
    re.I,
)
_COOKIE_HEADER_RE = re.compile(
    rf"(?P<prefix>\b(?:cookie|set-cookie)\b\s*[:=]\s*)"
    r"(?P<value>[^\n\r]+)",
    re.I,
)
_BARE_SECRET_RE = re.compile(
    rf"(?P<prefix>\b(?:{_BARE_SENSITIVE_KEY_PATTERN})\b\s*[:=]\s*)"
    r"(?P<value>[^\s,;}\])]+)",
    re.I,
)


class _ErrorLogHandler(logging.Handler):
    """Collect INFO+ records for the Admin error analysis view."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.INFO:
            return
        try:
            message = record.getMessage()
            exc_text = ""
            if record.exc_info:
                exc_text = "".join(traceback.format_exception(*record.exc_info))
            message = _redact_sensitive_text(message)
            exc_text = _redact_sensitive_text(exc_text)
            ctx = dict(_request_context.get() or {})
            merged = _merge_ids(ctx, message + "\n" + exc_text)
            _LOG_BUFFER.appendleft(
                {
                    "timestamp": datetime.datetime.fromtimestamp(
                        record.created,
                        tz=datetime.timezone.utc,
                    ).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": message,
                    "traceback": exc_text,
                    "path": ctx.get("path", ""),
                    "method": ctx.get("method", ""),
                    "session_id": merged.get("session_id", ""),
                    "user_id": merged.get("user_id", ""),
                    "material_id": merged.get("material_id", ""),
                    "course_id": merged.get("course_id", ""),
                }
            )
        except Exception:
            self.handleError(record)


def install_error_log_capture() -> None:
    """Install the in-memory ERROR log capture handler once."""

    global _HANDLER_INSTALLED
    if _HANDLER_INSTALLED:
        return
    handler = _ErrorLogHandler()
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)
    _HANDLER_INSTALLED = True


def register_error_log_middleware(app) -> None:
    """Attach request context middleware used by the log capture handler."""

    @app.middleware("http")
    async def error_log_context_middleware(request: Request, call_next):
        ctx = _extract_request_context(request)
        token = _request_context.set(ctx)
        try:
            return await call_next(request)
        except Exception:
            logging.getLogger("routes.error_logs").exception("Unhandled request error")
            raise
        finally:
            _request_context.reset(token)


def _extract_request_context(request: Request) -> dict[str, Any]:
    path = request.url.path
    query = request.query_params
    ctx: dict[str, Any] = {
        "method": request.method,
        "path": path,
    }
    for key in _ID_KEYS:
        if key in query:
            ctx[key] = query.get(key) or ""

    path_parts = [p for p in path.split("/") if p]
    for i, part in enumerate(path_parts[:-1]):
        nxt = path_parts[i + 1]
        if part in ("courses", "course"):
            ctx.setdefault("course_id", nxt)
        elif part in ("materials", "material", "documents", "document"):
            ctx.setdefault("material_id", nxt)
        elif part in ("sessions", "session"):
            ctx.setdefault("session_id", nxt)
        elif part in ("users", "user"):
            ctx.setdefault("user_id", nxt)

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            payload = jwt.decode(
                auth.split(" ", 1)[1],
                _get_settings().jwt_secret,
                algorithms=[_JWT_ALGORITHM],
            )
            ctx.setdefault("user_id", payload.get("sub", ""))
        except Exception:
            pass
    return ctx


def _merge_ids(ctx: dict[str, Any], text: str) -> dict[str, str]:
    merged = {key: str(ctx.get(key, "") or "") for key in _ID_KEYS}
    for key, pattern in _KEY_PATTERNS.items():
        if merged.get(key):
            continue
        match = pattern.search(text)
        if match:
            merged[key] = match.group(1).strip(".,;)]}")
    return merged


def _redact_sensitive_text(text: Any) -> str:
    """Mask common secret-bearing fields before logs reach the admin UI."""

    if text is None:
        return ""
    redacted = str(text)
    redacted = _QUOTED_SECRET_RE.sub(
        lambda m: f"{m.group('prefix')}{m.group('value_quote')}{_REDACTED}{m.group('value_quote')}",
        redacted,
    )
    redacted = _AUTH_HEADER_RE.sub(
        lambda m: f"{m.group('prefix')}{_REDACTED}",
        redacted,
    )
    redacted = _COOKIE_HEADER_RE.sub(
        lambda m: f"{m.group('prefix')}{_REDACTED}",
        redacted,
    )
    redacted = _BARE_SECRET_RE.sub(
        lambda m: f"{m.group('prefix')}{_REDACTED}",
        redacted,
    )
    return redacted


def _matches_keyword(row: dict[str, Any], keyword: str) -> bool:
    if not keyword:
        return True
    haystack = "\n".join(str(row.get(k, "") or "") for k in (
        "timestamp",
        "level",
        "logger",
        "message",
        "traceback",
        "path",
        "method",
        "session_id",
        "user_id",
        "material_id",
        "course_id",
    ))
    return keyword.lower() in haystack.lower()


def _matches_level(row: dict[str, Any], include_info: bool) -> bool:
    if include_info:
        return str(row.get("level", "")).upper() in {"INFO", "WARNING", "ERROR", "CRITICAL"}
    return str(row.get("level", "")).upper() in {"ERROR", "CRITICAL"}


@router.get("")
def list_error_logs(
    keyword: str = "",
    minutes: int = 1440,
    limit: int = 1000,
    include_info: bool = False,
    current_user: dict = Depends(_require_system_admin),
) -> dict[str, Any]:
    """Return recent admin logs filtered by level and an optional keyword."""

    now = datetime.datetime.now(datetime.timezone.utc)
    minutes = max(1, min(minutes, 10080))
    max_items = max(1, _get_settings().admin_error_log_max_items)
    limit = max(1, min(limit, max_items))
    since = now - datetime.timedelta(minutes=minutes)

    rows = []
    for row in list(_LOG_BUFFER):
        try:
            ts = datetime.datetime.fromisoformat(str(row["timestamp"]))
        except Exception:
            ts = now
        if ts < since:
            continue
        if not _matches_level(row, include_info):
            continue
        if not _matches_keyword(row, keyword.strip()):
            continue
        rows.append(row)
        if len(rows) >= limit:
            break

    return {
        "items": rows,
        "total_buffered": len(_LOG_BUFFER),
        "limit": limit,
        "minutes": minutes,
        "include_info": include_info,
        "max_items": max_items,
    }
