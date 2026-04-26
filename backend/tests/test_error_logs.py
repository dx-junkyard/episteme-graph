from __future__ import annotations

import datetime
import logging
import os
import sys

_backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_api_dir = os.path.join(_backend_dir, "api")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _api_dir not in sys.path:
    sys.path.insert(0, _api_dir)

from routes import error_logs  # noqa: E402


def _settings(max_items: int = 1000):
    return type("Settings", (), {"admin_error_log_max_items": max_items})()


def _row(level: str, message: str) -> dict:
    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "level": level,
        "logger": "test",
        "message": message,
        "traceback": "",
        "path": "",
        "method": "",
        "session_id": "",
        "user_id": "",
        "material_id": "",
        "course_id": "",
    }


def test_error_logs_default_to_error_and_critical_only(monkeypatch):
    monkeypatch.setattr(error_logs, "_get_settings", lambda: _settings())
    error_logs._LOG_BUFFER.clear()
    error_logs._LOG_BUFFER.append(_row("INFO", "info message"))
    error_logs._LOG_BUFFER.append(_row("ERROR", "error message"))

    result = error_logs.list_error_logs(current_user={"role": "SYSTEM_ADMIN"})

    assert [row["message"] for row in result["items"]] == ["error message"]
    assert result["limit"] == 1000
    assert result["include_info"] is False


def test_error_logs_can_include_info(monkeypatch):
    monkeypatch.setattr(error_logs, "_get_settings", lambda: _settings())
    error_logs._LOG_BUFFER.clear()
    error_logs._LOG_BUFFER.append(_row("INFO", "info message"))
    error_logs._LOG_BUFFER.append(_row("ERROR", "error message"))

    result = error_logs.list_error_logs(
        include_info=True,
        current_user={"role": "SYSTEM_ADMIN"},
    )

    assert [row["message"] for row in result["items"]] == ["info message", "error message"]


def test_error_log_limit_is_clamped_to_configured_max(monkeypatch):
    monkeypatch.setattr(
        error_logs,
        "_get_settings",
        lambda: _settings(max_items=2),
    )
    error_logs._LOG_BUFFER.clear()
    for idx in range(3):
        error_logs._LOG_BUFFER.append(_row("ERROR", f"error {idx}"))

    result = error_logs.list_error_logs(
        limit=1000,
        current_user={"role": "SYSTEM_ADMIN"},
    )

    assert result["limit"] == 2
    assert len(result["items"]) == 2


def test_redacts_sensitive_values_from_common_log_formats():
    text = "\n".join([
        "password=plain-secret",
        "api_key: sk-test-secret",
        '{"access_token":"token-secret","user":"alice"}',
        "Authorization: Bearer jwt-secret",
        "Cookie: session=secret; csrf=secret",
        "client_secret='oauth-secret'",
    ])

    redacted = error_logs._redact_sensitive_text(text)

    assert "plain-secret" not in redacted
    assert "sk-test-secret" not in redacted
    assert "token-secret" not in redacted
    assert "jwt-secret" not in redacted
    assert "session=secret" not in redacted
    assert "oauth-secret" not in redacted
    assert redacted.count("[REDACTED]") >= 6
    assert '"user":"alice"' in redacted


def test_log_handler_stores_redacted_message_and_traceback():
    error_logs._LOG_BUFFER.clear()
    try:
        raise RuntimeError("failure with refresh_token=trace-secret")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg='failed login {"password":"body-secret"} Authorization: Bearer header-secret',
        args=(),
        exc_info=exc_info,
    )

    error_logs._ErrorLogHandler().emit(record)
    stored = error_logs._LOG_BUFFER[0]

    assert "body-secret" not in stored["message"]
    assert "header-secret" not in stored["message"]
    assert "trace-secret" not in stored["traceback"]
    assert "[REDACTED]" in stored["message"]
    assert "[REDACTED]" in stored["traceback"]
