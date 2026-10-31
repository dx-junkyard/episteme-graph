"""IK-0365: 提供元の利用上限・残高切れでは、コースビルダーの縮退文を「待てば直る」文面にしない。

教員向けのコースビルダーだけ、管理者に確認を依頼する事実文に分ける。数値・提供元の生メッセージ・
URL は出さない。学習者チャットの文面は変えない。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_API_DIR = str(Path(__file__).resolve().parents[1] / "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

import routes.admin as routes_admin  # noqa: E402


class RateLimitError(Exception):
    """provider SDK の例外を模したもの（型名で判定されることの確認）。"""


class _WrappedError(RuntimeError):
    pass


_RAW = "Error code: 429 - {'error': {'code': 'insufficient_quota', 'message': 'see https://platform.example/billing'}}"


@pytest.mark.parametrize("exc", [
    RateLimitError("Too Many Requests"),
    RuntimeError(_RAW),
    RuntimeError("Your credit balance is too low"),
])
def test_quota_errors_get_the_provider_fact_sentence(exc):
    message = routes_admin._course_builder_degraded_message(exc)
    assert message == routes_admin._COURSE_BUILDER_PROVIDER_QUOTA_MESSAGE
    assert "しばらくしてから" not in message
    assert "システム管理者" in message
    # 生メッセージ・URL・数値を出さない。
    assert "http" not in message and "429" not in message
    assert not any(ch.isdigit() for ch in message)


def test_wrapped_quota_error_is_classified_through_the_cause_chain():
    try:
        try:
            raise RateLimitError("quota")
        except RateLimitError as inner:
            raise _WrappedError("LLM call failed") from inner
    except _WrappedError as outer:
        assert routes_admin._is_provider_quota_error(outer) is True


def test_other_failures_keep_the_temporary_message():
    assert routes_admin._course_builder_degraded_message(TimeoutError("read timeout")) == (
        routes_admin._COURSE_BUILDER_DEGRADED_MESSAGE
    )
    assert routes_admin._is_provider_quota_error(None) is False


try:
    from fastapi.testclient import TestClient
    _HAS_FASTAPI = True
except (ImportError, RuntimeError):  # pragma: no cover
    _HAS_FASTAPI = False


@pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed")
def test_course_builder_chat_returns_the_quota_message(monkeypatch):
    from api.main import app
    from core.llm_worker.cost_gate import CostGate
    from dependencies import ROLE_TEACHER, _create_token

    def boom(*_a, **_k):
        raise RateLimitError(_RAW)

    monkeypatch.setattr(routes_admin, "_course_builder_cost_gate", CostGate())
    monkeypatch.setattr(routes_admin, "generate_text", boom)
    monkeypatch.setattr(routes_admin, "save_cb_session", lambda *a, **k: True)
    token = _create_token("33333333-3333-3333-3333-333333333333", "teacher1", "t@test.com", ROLE_TEACHER)
    resp = TestClient(app).post(
        "/api/admin/course-builder/chat",
        json={"message": "作って", "selected_material_ids": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["degraded"] is True
    assert data["answer"] == routes_admin._COURSE_BUILDER_PROVIDER_QUOTA_MESSAGE
    assert "insufficient_quota" not in data["answer"]


def test_learner_chat_does_not_use_the_teacher_message():
    src = (Path(__file__).resolve().parents[1] / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
    assert "_COURSE_BUILDER_PROVIDER_QUOTA_MESSAGE" not in src
    assert "システム管理者に提供元の設定・残高の確認を依頼してください" not in src
