"""Pin the single-block request unit for the tests written against it.

IK-0699 (2026-10-22) made windowed judgement the default (several blocks per
LLM call). The tests in this directory that mock one response per block exercise
the single-block path — which stays the window fallback — so they run with
``RHETORICAL_ROLE_BLOCKS_PER_CALL=1``. Windowing and the checkpoint are tested in
``test_windowing.py``, which passes ``config["blocks_per_call"]`` explicitly
(``config`` takes precedence over env).
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _single_block_request_unit(monkeypatch):
    monkeypatch.setenv("RHETORICAL_ROLE_BLOCKS_PER_CALL", "1")
    monkeypatch.delenv("RHETORICAL_ROLE_WINDOW_MAX_CHARS", raising=False)
