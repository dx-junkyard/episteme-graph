"""コースビルダーチャットの共通基盤整理（Phase 2-C）テスト。

正本: docs/features/assistant_common_infra_design.md §1（コスト上限）/
§2-2（会話履歴ウィンドウ化・head_keep=2）/ §3（モデル解決）/ §4（縮退統一）/
§6（save_cb_session 正直化）。

TestClient + monkeypatch（`test_course_builder_context.py` と同型）。実 DB・実 LLM
は使わず、`generate_text` / `save_cb_session` / `get_settings` を差し替えて検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure api/ is importable (test_course_builder_context.py と同じ流儀)
_backend_dir = str(Path(__file__).resolve().parents[1])
_api_dir = str(Path(__file__).resolve().parents[1] / "api")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)
if _api_dir not in sys.path:
    sys.path.insert(0, _api_dir)

try:
    import routes.admin as _routes_admin_mod  # noqa: F401
except Exception:
    pass

# モジュールレベルで束縛する: core.llm_worker.client も "from core.config import
# get_settings" で同じ実体を束縛しているため、テスト関数内で改めて import すると
# conftest._override_settings の monkeypatch（"core.config.get_settings" 属性の
# 差し替え）後の別オブジェクトを掴んでしまい resolve_model() 内部の参照とズレる。
from core.config import get_settings as _real_get_settings
from core.llm_worker.client import resolve_model

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient
except (ImportError, RuntimeError):
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI not installed (run in Docker for full API tests)")


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


@pytest.fixture
def teacher_token():
    from dependencies import _create_token, ROLE_TEACHER
    return _create_token("33333333-3333-3333-3333-333333333333", "teacher1", "teacher1@test.com", ROLE_TEACHER)


@pytest.fixture
def auth_headers(teacher_token):
    return {"Authorization": f"Bearer {teacher_token}"}


@pytest.fixture(autouse=True)
def _fresh_cost_gate():
    """モジュールレベル CostGate はプロセスに1つなので、テスト間の干渉を避けるため
    毎テストで新しいインスタンスに差し替える。"""
    import routes.admin as route_mod
    from core.llm_worker.cost_gate import CostGate

    route_mod._course_builder_cost_gate = CostGate()
    yield


class _FakeSettings:
    """course_builder_chat が参照する属性だけを持つダミー設定。"""

    def __init__(self, course_builder_max_calls_per_day: int = 100):
        self.course_builder_max_calls_per_day = course_builder_max_calls_per_day


# ===========================================================================
# 1. コスト上限（CostGate）
# ===========================================================================

class TestCostGate:
    def test_quota_exceeded_returns_429_without_numbers(self, client, auth_headers, monkeypatch):
        import routes.admin as route_mod

        monkeypatch.setattr(route_mod, "get_settings", lambda: _FakeSettings(course_builder_max_calls_per_day=0))
        mock_calls = []
        monkeypatch.setattr(route_mod, "generate_text", lambda *a, **k: mock_calls.append(1) or "unused")

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={"message": "コースを作って", "selected_material_ids": []},
            headers=auth_headers,
        )
        assert resp.status_code == 429
        detail = resp.json()["detail"]
        # I2: 数値（残数・上限値）を見せない
        assert not any(ch.isdigit() for ch in detail)
        # ゲートで弾かれるので LLM は呼ばれない
        assert mock_calls == []

    def test_within_quota_does_not_block(self, client, auth_headers, monkeypatch):
        import routes.admin as route_mod

        monkeypatch.setattr(route_mod, "get_settings", lambda: _FakeSettings(course_builder_max_calls_per_day=100))
        monkeypatch.setattr(route_mod, "generate_text", lambda *a, **k: "テスト応答")
        monkeypatch.setattr(route_mod, "save_cb_session", lambda *a, **k: True)

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={"message": "コースを作って", "selected_material_ids": []},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["degraded"] is False


# ===========================================================================
# 2. 会話履歴ウィンドウ化（head_keep=2）
# ===========================================================================

class TestHistoryWindowing:
    def test_head_two_protected_middle_dropped_tail_kept(self, client, auth_headers, monkeypatch):
        """フロントの draftContext/draftAck 疑似ターン（先頭2件）が保護され、
        直近20件のみが末尾から残ること（中間は落ちる）。"""
        import routes.admin as route_mod

        history = [
            {"role": "user", "content": "DRAFT_HEAD_USER"},
            {"role": "assistant", "content": "DRAFT_HEAD_ASSISTANT"},
        ]
        for i in range(30):
            history.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"filler-{i}"})

        captured = {}

        def fake_generate_text(messages, **kwargs):
            captured["messages"] = messages
            return "テスト応答"

        monkeypatch.setattr(route_mod, "generate_text", fake_generate_text)
        monkeypatch.setattr(route_mod, "save_cb_session", lambda *a, **k: True)

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={"message": "final question", "history": history, "selected_material_ids": []},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        messages = captured["messages"]
        contents = [m["content"] for m in messages]

        assert "DRAFT_HEAD_USER" in contents
        assert "DRAFT_HEAD_ASSISTANT" in contents
        # 中間 (filler-0..filler-9) は落ちる
        assert "filler-0" not in contents
        assert "filler-9" not in contents
        # 末尾20件 (filler-10..filler-29) は残る
        assert "filler-10" in contents
        assert "filler-29" in contents
        # system + head(2) + tail(20) + final user message = 24
        assert len(messages) == 24

    def test_short_history_passes_through_unwindowed(self, client, auth_headers, monkeypatch):
        import routes.admin as route_mod

        history = [
            {"role": "user", "content": "short-1"},
            {"role": "assistant", "content": "short-2"},
        ]
        captured = {}

        def fake_generate_text(messages, **kwargs):
            captured["messages"] = messages
            return "テスト応答"

        monkeypatch.setattr(route_mod, "generate_text", fake_generate_text)
        monkeypatch.setattr(route_mod, "save_cb_session", lambda *a, **k: True)

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={"message": "final question", "history": history, "selected_material_ids": []},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        contents = [m["content"] for m in captured["messages"]]
        assert "short-1" in contents
        assert "short-2" in contents

    def test_saved_session_history_is_not_windowed(self, client, auth_headers, monkeypatch):
        """save_cb_session に渡す履歴は全件保存のまま（ウィンドウ化しない）。"""
        import routes.admin as route_mod

        history = [{"role": "user", "content": f"filler-{i}"} for i in range(40)]
        saved = {}

        def fake_save(user_id, session_id, hist, draft):
            saved["history"] = hist
            return True

        monkeypatch.setattr(route_mod, "generate_text", lambda *a, **k: "テスト応答")
        monkeypatch.setattr(route_mod, "save_cb_session", fake_save)

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={
                "message": "final question",
                "history": history,
                "session_id": "sess-window-1",
                "selected_material_ids": [],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        # 元の40件 + 今回のuser/assistant往復2件 = 42件がそのまま保存される
        assert len(saved["history"]) == 42


# ===========================================================================
# 3. 縮退統一（degraded）
# ===========================================================================

class TestDegraded:
    def test_llm_exception_returns_200_degraded_and_saves_session(self, client, auth_headers, monkeypatch):
        import routes.admin as route_mod

        def boom(*a, **k):
            raise RuntimeError("LLM down")

        saved = {}

        def fake_save(user_id, session_id, hist, draft):
            saved["history"] = hist
            saved["draft"] = draft
            return True

        monkeypatch.setattr(route_mod, "generate_text", boom)
        monkeypatch.setattr(route_mod, "save_cb_session", fake_save)

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={
                "message": "コースを作って",
                "session_id": "sess-degraded-1",
                "selected_material_ids": [],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded"] is True
        assert data["course_draft"] is None
        assert data["answer"] == "AI 応答を生成できませんでした。しばらくしてからもう一度お試しください。"
        # degraded ターンも履歴に保存される (I4)
        assert saved["history"][-1]["content"] == data["answer"]
        assert saved["history"][-2]["content"] == "コースを作って"

    def test_success_path_is_not_degraded(self, client, auth_headers, monkeypatch):
        import routes.admin as route_mod

        monkeypatch.setattr(route_mod, "generate_text", lambda *a, **k: "普通の応答")
        monkeypatch.setattr(route_mod, "save_cb_session", lambda *a, **k: True)

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={"message": "コースを作って", "selected_material_ids": []},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["degraded"] is False
        assert data["answer"] == "普通の応答"


# ===========================================================================
# 4. save_cb_session の正直化（session_saved）
# ===========================================================================

class TestSessionSaved:
    def test_session_saved_false_when_save_fails(self, client, auth_headers, monkeypatch):
        import routes.admin as route_mod

        monkeypatch.setattr(route_mod, "generate_text", lambda *a, **k: "テスト応答")
        monkeypatch.setattr(route_mod, "save_cb_session", lambda *a, **k: False)

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={
                "message": "コースを作って",
                "session_id": "sess-fail-1",
                "selected_material_ids": [],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_saved"] is False
        # 応答本体は保存失敗でも失われない
        assert data["answer"] == "テスト応答"

    def test_session_saved_true_when_save_succeeds(self, client, auth_headers, monkeypatch):
        import routes.admin as route_mod

        monkeypatch.setattr(route_mod, "generate_text", lambda *a, **k: "テスト応答")
        monkeypatch.setattr(route_mod, "save_cb_session", lambda *a, **k: True)

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={
                "message": "コースを作って",
                "session_id": "sess-ok-1",
                "selected_material_ids": [],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["session_saved"] is True

    def test_session_saved_true_by_default_when_no_session_id(self, client, auth_headers, monkeypatch):
        """session_id が無ければ保存自体を行わないので session_saved は既定 True。"""
        import routes.admin as route_mod

        save_called = []
        monkeypatch.setattr(route_mod, "generate_text", lambda *a, **k: "テスト応答")
        monkeypatch.setattr(route_mod, "save_cb_session", lambda *a, **k: save_called.append(1) or True)

        resp = client.post(
            "/api/admin/course-builder/chat",
            json={"message": "コースを作って", "selected_material_ids": []},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["session_saved"] is True
        assert save_called == []


class TestSaveCbSessionReturnsBool:
    """services.save_cb_session 自体が bool を返すこと（DB 例外時は False）。"""

    def test_returns_true_on_success(self, monkeypatch):
        import services

        class _FakeSession:
            def execute(self, *a, **k):
                return None

            def commit(self):
                return None

            def rollback(self):
                return None

            def close(self):
                return None

        monkeypatch.setattr(services, "_pg_session", lambda: _FakeSession())
        result = services.save_cb_session("user-1", "sess-1", [{"role": "user", "content": "hi"}], None)
        assert result is True

    def test_returns_false_on_db_exception(self, monkeypatch):
        import services

        class _FailingSession:
            def execute(self, *a, **k):
                raise RuntimeError("db down")

            def rollback(self):
                return None

            def close(self):
                return None

        monkeypatch.setattr(services, "_pg_session", lambda: _FailingSession())
        result = services.save_cb_session("user-1", "sess-1", [{"role": "user", "content": "hi"}], None)
        assert result is False


# ===========================================================================
# 5. モデル解決（resolve_model）
# ===========================================================================

class TestResolveModelFallback:
    def test_course_builder_model_falls_back_to_analysis_when_unset(self, monkeypatch):
        monkeypatch.delenv("COURSE_BUILDER_LLM_MODEL", raising=False)
        _real_get_settings.cache_clear()
        try:
            settings = _real_get_settings()
            assert settings.course_builder_llm_model == ""
            resolved = resolve_model("course_builder_llm_model", fallback="analysis")
            assert resolved == settings.llm_analysis_model
        finally:
            _real_get_settings.cache_clear()

    def test_explicit_env_overrides_fallback(self, monkeypatch):
        monkeypatch.setenv("COURSE_BUILDER_LLM_MODEL", "gpt-custom-course-builder")
        _real_get_settings.cache_clear()
        try:
            resolved = resolve_model("course_builder_llm_model", fallback="analysis")
            assert resolved == "gpt-custom-course-builder"
        finally:
            _real_get_settings.cache_clear()
