"""SB層 sandbox クライアント（§5.5）— transport 注入でネットワークを出さずに応答の写像を固定する。"""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402

from core.simulation_bridge import schema  # noqa: E402
from core.simulation_bridge.sandbox import (  # noqa: E402
    SandboxClient,
    SandboxUnreachable,
    sandbox_client_from_settings,
)

SRC = "def simulate(inputs, params):\n    return {'y': 1}\n"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("ネットワークに出てはいけない")

    monkeypatch.setattr("urllib.request.urlopen", _boom)


def _client(transport):
    return SandboxClient("http://sim-runner:8080", timeout_s=5, transport=transport)


def test_ok_maps_to_succeeded_and_sends_payload():
    seen: list[dict] = []

    def transport(payload):
        seen.append(payload)
        return {"ok": True, "outputs": {"y": [1.0, 2.0]}, "error": "", "stderr_tail": ""}

    result = _client(transport).run(SRC, entrypoint="simulate", inputs={"x": 1}, params={"g": 2}, seed=3)
    assert result.status == schema.RUN_STATUS_SUCCEEDED
    assert result.outputs == {"y": [1.0, 2.0]}
    assert seen[0]["source"] == SRC
    assert seen[0]["entrypoint"] == "simulate"
    assert seen[0]["inputs"] == {"x": 1} and seen[0]["params"] == {"g": 2}
    assert seen[0]["seed"] == 3 and seen[0]["timeout_s"] == 5.0


def test_runner_error_maps_to_failed_with_clipped_clean_text():
    def transport(_payload):
        return {"ok": False, "error": "ValueError: \x1b[31mbad\x1b[0m " + "x" * 2000, "stderr_tail": "trace"}

    result = _client(transport).run(SRC, entrypoint="simulate", inputs={}, params={})
    assert result.status == schema.RUN_STATUS_FAILED
    assert "\x1b" not in result.error
    assert len(result.error) <= 500
    assert result.error.startswith("ValueError")


def test_not_configured_does_not_call_transport():
    called = []
    client = SandboxClient("", timeout_s=5, transport=lambda p: called.append(p) or {})
    result = client.run(SRC, entrypoint="simulate", inputs={}, params={})
    assert result.status == schema.RUN_STATUS_FAILED
    assert result.error == "not_configured"
    assert result.facts == [schema.FACT_SANDBOX_NOT_CONFIGURED]
    assert called == []


@pytest.mark.parametrize("exc", [SandboxUnreachable("refused"), socket.timeout("t"), ConnectionError("c")])
def test_unreachable_maps_to_fact(exc):
    def transport(_payload):
        raise exc

    result = _client(transport).run(SRC, entrypoint="simulate", inputs={}, params={})
    assert result.status == schema.RUN_STATUS_FAILED
    assert result.facts == [schema.FACT_SANDBOX_UNREACHABLE]


def test_malformed_responses():
    for response in ("not a dict", {"ok": True, "outputs": [1, 2]}):
        result = _client(lambda _p, r=response: r).run(SRC, entrypoint="simulate", inputs={}, params={})
        assert result.status == schema.RUN_STATUS_FAILED
        assert result.error


def test_http_error_from_transport_is_failed_without_unreachable_fact():
    def transport(_payload):
        raise RuntimeError("HTTP 500: boom")

    result = _client(transport).run(SRC, entrypoint="simulate", inputs={}, params={})
    assert result.status == schema.RUN_STATUS_FAILED
    assert "HTTP 500" in result.error
    assert result.facts == []


def test_default_transport_unreachable_without_real_network(monkeypatch):
    import urllib.error

    def _refuse(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _refuse)
    result = SandboxClient("http://sim-runner:8080", timeout_s=1).run(SRC, entrypoint="simulate", inputs={}, params={})
    assert result.facts == [schema.FACT_SANDBOX_UNREACHABLE]


def test_from_settings():
    client = sandbox_client_from_settings(SimpleNamespace(sim_runner_url="http://x:8080/", simulation_run_timeout_seconds=7))
    assert client.base_url == "http://x:8080"
    assert client.timeout_s == 7.0
    assert not sandbox_client_from_settings(SimpleNamespace()).configured
