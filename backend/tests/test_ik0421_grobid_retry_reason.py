"""IK-0421: GROBID の失敗理由を artifact に残し、混雑（503）・接続失敗は短く再試行する。"""
from __future__ import annotations

from unittest.mock import patch

import requests

from core.document_pipeline import orchestrator


def _http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"{status} Server Error", response=resp)


def test_retries_on_503_then_succeeds_and_records_attempts():
    calls = {"n": 0}

    def _grobid(pdf_bytes):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(503)
        return "<TEI/>"

    slept: list[float] = []
    with patch.object(orchestrator, "_run_grobid_parse", side_effect=_grobid):
        tei, reason, attempts = orchestrator._grobid_parse_with_retry(b"%PDF", "doc", sleep=slept.append)
    assert tei == "<TEI/>" and reason is None and attempts == 3
    assert slept == list(orchestrator.GROBID_RETRY_DELAYS_S)


def test_non_retryable_error_falls_back_at_once_with_reason():
    def _grobid(pdf_bytes):
        raise _http_error(400)

    slept: list[float] = []
    with patch.object(orchestrator, "_run_grobid_parse", side_effect=_grobid):
        tei, reason, attempts = orchestrator._grobid_parse_with_retry(b"%PDF", "doc", sleep=slept.append)
    assert tei is None and attempts == 1 and slept == []
    assert reason.startswith("HTTPError: 400")


def test_builtin_connection_error_is_not_retried():
    def _grobid(pdf_bytes):
        raise ConnectionError("GROBID not available")

    slept: list[float] = []
    with patch.object(orchestrator, "_run_grobid_parse", side_effect=_grobid):
        tei, reason, attempts = orchestrator._grobid_parse_with_retry(b"%PDF", "doc", sleep=slept.append)
    assert tei is None and attempts == 1 and slept == [] and reason == "ConnectionError: GROBID not available"


def test_requests_connection_error_is_retried_then_gives_reason():
    def _grobid(pdf_bytes):
        raise requests.ConnectionError("refused")

    slept: list[float] = []
    with patch.object(orchestrator, "_run_grobid_parse", side_effect=_grobid):
        tei, reason, attempts = orchestrator._grobid_parse_with_retry(b"%PDF", "doc", sleep=slept.append)
    assert tei is None and attempts == len(orchestrator.GROBID_RETRY_DELAYS_S) + 1
    assert reason == "ConnectionError: refused"


def test_stage_source_records_reason_in_artifact():
    import inspect

    src = inspect.getsource(orchestrator._stage_grobid_parse)
    assert '"fallback_reason": fallback_reason' in src and '"attempts": attempts' in src
