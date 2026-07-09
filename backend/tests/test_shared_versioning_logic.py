"""V層の純ロジックテスト（DB 非依存・DB 関数は monkeypatch）。

core.versioning の import に sqlalchemy 等が必要なため、未導入環境（CI-lite）では skip する。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_CAN_IMPORT = True
try:
    from core.versioning import deletion as _deletion
    from core.versioning import resolver as _resolver
    from core.versioning import releases as _releases
    from core.versioning import subscriptions as _subs
    import services as _services
except Exception:  # noqa: BLE001 - 依存未導入
    _CAN_IMPORT = False

pytestmark = pytest.mark.skipif(not _CAN_IMPORT, reason="core.versioning の依存（sqlalchemy 等）未導入")


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeSession:
    """course_deletion_notice の learning_courses 参照だけを差し替える最小セッション。"""

    def __init__(self, row):
        self._row = row

    def execute(self, *a, **k):
        return _FakeResult(self._row)

    def close(self):
        pass


class TestCourseDeletionNotice:
    """Issue 2a: コース自体 / 元教材の削除予約を学習者バナー用に検出する。"""

    def test_course_itself_pending(self, monkeypatch):
        monkeypatch.setattr(_releases, "get_state",
                            lambda ot, oid: {"lifecycle": "pending_deletion",
                                             "delete_purge_after": "2099-01-01T00:00:00+00:00",
                                             "delete_reason": "古い"} if ot == "course" else None)
        out = _services.course_deletion_notice("c1")
        assert out and out["lifecycle"] == "pending_deletion"
        assert out["delete_reason"] == "古い"

    def test_source_document_pending_propagates(self, monkeypatch):
        # コースは active、参照教材 docA が pending_deletion（所有者一致）→ バナーを出す
        def _state(ot, oid):
            if ot == "course":
                return {"lifecycle": "active"}
            return {"lifecycle": "pending_deletion",
                    "delete_purge_after": "2099-05-05T00:00:00+00:00", "delete_reason": ""}
        monkeypatch.setattr(_releases, "get_state", _state)
        monkeypatch.setattr(_services, "_pg_session",
                            lambda: _FakeSession(({"sources": [{"material_id": "matA"}]}, "owner-1")))
        monkeypatch.setattr(_services, "_resolve_document",
                            lambda ref: {"id": "docA", "uploaded_by": "owner-1"})
        out = _services.course_deletion_notice("c1")
        assert out and out["lifecycle"] == "pending_deletion"
        assert out["delete_purge_after"].startswith("2099-05")
        # 理由が空でも教材由来であることを補う
        assert "教材" in out["delete_reason"]

    def test_source_document_owned_by_other_is_ignored(self, monkeypatch):
        # 参照教材の所有者がコース所有者と異なる場合は巻き添え削除されない → バナーを出さない
        def _state(ot, oid):
            return {"lifecycle": "active"} if ot == "course" else {"lifecycle": "pending_deletion",
                                                                    "delete_purge_after": "2099-05-05T00:00:00+00:00"}
        monkeypatch.setattr(_releases, "get_state", _state)
        monkeypatch.setattr(_services, "_pg_session",
                            lambda: _FakeSession(({"sources": [{"material_id": "matA"}]}, "owner-1")))
        monkeypatch.setattr(_services, "_resolve_document",
                            lambda ref: {"id": "docA", "uploaded_by": "someone-else"})
        assert _services.course_deletion_notice("c1") is None

    def test_no_versioning_returns_none(self, monkeypatch):
        monkeypatch.setattr(_releases, "get_state", lambda ot, oid: None)
        monkeypatch.setattr(_services, "_pg_session",
                            lambda: _FakeSession(({"sources": []}, "owner-1")))
        assert _services.course_deletion_notice("c1") is None


class TestDefaultPurgeAfter:
    def test_default_is_about_14_days(self):
        pa = _deletion.default_purge_after()
        delta = pa - datetime.now(timezone.utc)
        assert 13 <= delta.days <= 14

    def test_custom_days(self):
        pa = _deletion.default_purge_after(3)
        delta = pa - datetime.now(timezone.utc)
        assert 2 <= delta.days <= 3

    def test_is_timezone_aware(self):
        assert _deletion.default_purge_after(1).tzinfo is not None


class TestViewBadges:
    """update_available / lifecycle の導出（DB 関数は monkeypatch）。"""

    def test_no_versioning_when_state_missing(self, monkeypatch):
        monkeypatch.setattr(_releases, "get_state", lambda ot, oid: None)
        out = _resolver.view_badges("course", "c1", "u1")
        assert out == {"has_versioning": False}

    def test_update_available_when_pinned_behind_active(self, monkeypatch):
        monkeypatch.setattr(_releases, "get_state",
                            lambda ot, oid: {"active_release_id": "A", "latest_version_no": 2, "lifecycle": "active"})
        monkeypatch.setattr(_subs, "resolve_effective_release_id", lambda ot, oid, uid: "P")
        monkeypatch.setattr(_subs, "get_subscription", lambda ot, oid, uid: {"pinned_release_id": "P"})
        monkeypatch.setattr(_releases, "get_release", lambda rid: {"version_no": 1})
        out = _resolver.view_badges("course", "c1", "u1")
        assert out["has_versioning"] is True
        assert out["update_available"] is True
        assert out["pinned_version_no"] == 1
        assert out["latest_version_no"] == 2

    def test_no_update_when_pinned_equals_active(self, monkeypatch):
        monkeypatch.setattr(_releases, "get_state",
                            lambda ot, oid: {"active_release_id": "A", "latest_version_no": 2, "lifecycle": "active"})
        monkeypatch.setattr(_subs, "resolve_effective_release_id", lambda ot, oid, uid: "A")
        monkeypatch.setattr(_subs, "get_subscription", lambda ot, oid, uid: {"pinned_release_id": "A"})
        monkeypatch.setattr(_releases, "get_release", lambda rid: {"version_no": 2})
        out = _resolver.view_badges("course", "c1", "u1")
        assert out["update_available"] is False

    def test_no_update_when_unpinned(self, monkeypatch):
        # 未ピン viewer は active 追従 → update_available は False（取り込む対象が無い）
        monkeypatch.setattr(_releases, "get_state",
                            lambda ot, oid: {"active_release_id": "A", "latest_version_no": 1, "lifecycle": "active"})
        monkeypatch.setattr(_subs, "resolve_effective_release_id", lambda ot, oid, uid: "A")
        monkeypatch.setattr(_subs, "get_subscription", lambda ot, oid, uid: None)
        out = _resolver.view_badges("course", "c1", "u1")
        assert out["update_available"] is False
        assert out["pinned_release_id"] is None

    def test_pending_deletion_surfaced(self, monkeypatch):
        monkeypatch.setattr(_releases, "get_state",
                            lambda ot, oid: {"active_release_id": "A", "latest_version_no": 1,
                                             "lifecycle": "pending_deletion",
                                             "delete_purge_after": "2099-01-01T00:00:00+00:00"})
        monkeypatch.setattr(_subs, "resolve_effective_release_id", lambda ot, oid, uid: "A")
        monkeypatch.setattr(_subs, "get_subscription", lambda ot, oid, uid: None)
        out = _resolver.view_badges("document", "d1", "u1")
        assert out["lifecycle"] == "pending_deletion"
        assert out["delete_purge_after"].startswith("2099")
