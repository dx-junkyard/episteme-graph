"""``POST /api/admin/help-kb/refresh``（Phase 2 §2-2）— 権限・監査・キャッシュクリアの検証。

設計書 docs/features/manual_help_kb_design.md §2-2:
  - volume-mount 開発や hotfix の非常口であり、運用の主経路にはしない。
  - SYSTEM_ADMIN のみ（fail-closed）。
  - core.help_kb.manual.clear_manual_cache() + core.admin_assistant.knowledge.clear_cache()
    を呼び、再構築後の状態（audience別節数・validator 違反数・除外節数）を返す。
  - 監査は core.schema.AUDIT_ENTITY_MANUAL 経由で services.record_review_event に記録する
    （生リテラルの entity_type は使わない — test_audit_entity_catalog_guardrails.py が拒否する）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAS_FASTAPI = True
except Exception:  # pragma: no cover
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not _HAS_FASTAPI, reason="FastAPI 未導入（full API は Docker 内で）")

_UID_ADMIN = "33333333-3333-3333-3333-333333333333"


def _headers(role: str, sub: str = _UID_ADMIN):
    import jwt

    payload = {"sub": sub, "role": role, "username": "u1", "email": "u1@test.com"}
    return {"Authorization": "Bearer " + jwt.encode(payload, "test-secret-key", algorithm="HS256")}


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    import routes.admin_assistant as route_mod

    events: list = []
    monkeypatch.setattr(
        route_mod, "_record_manual_event",
        lambda entity_id, new_status, uid, meta=None: events.append((entity_id, new_status, uid, meta)),
    )

    cleared = {"manual": False, "kb": False}
    monkeypatch.setattr(route_mod.kb, "clear_cache", lambda: cleared.__setitem__("kb", True))
    if route_mod._help_kb_manual is not None:
        monkeypatch.setattr(
            route_mod._help_kb_manual, "clear_manual_cache",
            lambda: cleared.__setitem__("manual", True),
        )

    client = TestClient(app)
    return client, events, cleared, route_mod


class TestRefreshPermissions:
    """SYSTEM_ADMIN 以外は fail-closed で拒否する。"""

    def test_teacher_forbidden(self, api):
        client, events, _cleared, _route_mod = api
        resp = client.post("/api/admin/help-kb/refresh", headers=_headers("TEACHER"))
        assert resp.status_code == 403
        assert events == []

    def test_student_forbidden(self, api):
        client, events, _cleared, _route_mod = api
        resp = client.post("/api/admin/help-kb/refresh", headers=_headers("STUDENT"))
        assert resp.status_code == 403
        assert events == []

    def test_no_auth_is_rejected(self, api):
        client, events, _cleared, _route_mod = api
        resp = client.post("/api/admin/help-kb/refresh")
        assert resp.status_code in (401, 403)
        assert events == []


class TestRefreshBehavior:
    def test_system_admin_triggers_refresh(self, api):
        client, events, cleared, _route_mod = api
        resp = client.post("/api/admin/help-kb/refresh", headers=_headers("SYSTEM_ADMIN"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "refreshed"
        assert isinstance(body["audience_section_counts"], dict)
        assert isinstance(body["validator_violations"], int)
        assert isinstance(body["excluded_sections"], int)
        # 数値は事実のみ（confidence 等の生値がレスポンスに混ざらない）。
        assert set(body.keys()) == {
            "status", "audience_section_counts", "validator_violations", "excluded_sections",
        }

    def test_clears_both_caches(self, api):
        client, _events, cleared, route_mod = api
        client.post("/api/admin/help-kb/refresh", headers=_headers("SYSTEM_ADMIN"))
        assert cleared["kb"] is True
        if route_mod._help_kb_manual is not None:
            assert cleared["manual"] is True

    def test_records_audit_event_with_catalog_entity_type(self, api):
        from core.schema import AUDIT_ENTITY_MANUAL

        client, events, _cleared, _route_mod = api
        client.post("/api/admin/help-kb/refresh", headers=_headers("SYSTEM_ADMIN", sub=_UID_ADMIN))
        assert len(events) == 1
        entity_id, new_status, uid, metadata = events[0]
        assert new_status == "refreshed"
        assert uid == _UID_ADMIN
        assert isinstance(metadata, dict)
        assert "audience_section_counts" in metadata
        assert "validator_violations" in metadata
        assert "excluded_sections" in metadata
        # entity_type そのものは _record_manual_event 内部で AUDIT_ENTITY_MANUAL 固定。
        assert AUDIT_ENTITY_MANUAL == "manual"

    def test_idempotent_multiple_calls(self, api):
        """何度呼んでも 200 で同じ形の応答（再構築のみ・状態を壊さない）。"""
        client, events, _cleared, _route_mod = api
        for _ in range(3):
            resp = client.post("/api/admin/help-kb/refresh", headers=_headers("SYSTEM_ADMIN"))
            assert resp.status_code == 200
        assert len(events) == 3


class TestRefreshRouting:
    def test_final_path_is_help_kb_refresh_not_under_assistant_prefix(self, api):
        """最終パスは /api/admin/help-kb/refresh（/api/admin/assistant/... 配下ではない）。"""
        client, _events, _cleared, _route_mod = api
        ok = client.post("/api/admin/help-kb/refresh", headers=_headers("SYSTEM_ADMIN"))
        assert ok.status_code == 200
        wrong = client.post("/api/admin/assistant/help-kb/refresh", headers=_headers("SYSTEM_ADMIN"))
        assert wrong.status_code == 404
