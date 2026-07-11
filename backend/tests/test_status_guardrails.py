"""状態管理・通知基盤のガードレール（構造テスト・実 DB / FastAPI 不要）。

設計書 §5 の6項目に対応:
- core/status/ は FastAPI / LLM クライアントを import しない（S3）
- watcher の再実行で status_events が増えない（冪等性 = UNIQUE 制約 + ON CONFLICT）
- 通知の宛先が所有者・共有 editor 以外に広がらない（S5 fail-closed）
- 既読・却下が行削除しない（S4）
- 投影不能エンティティが unknown を返す（例外で落ちない）
- v1 の通知 kind が6種を超えない（段階登録の構造的強制）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_CORE_DIR = BACKEND / "core" / "status"
_SCHEMA_SRC = (_CORE_DIR / "schema.py").read_text(encoding="utf-8")
_PROJECTOR_SRC = (_CORE_DIR / "projector.py").read_text(encoding="utf-8")
_WATCHER_SRC = (_CORE_DIR / "watcher.py").read_text(encoding="utf-8")
_NOTIF_RULES_SRC = (_CORE_DIR / "notification_rules.py").read_text(encoding="utf-8")
_INBOX_SRC = (_CORE_DIR / "inbox.py").read_text(encoding="utf-8")
_STATUS_ROUTE_SRC = (BACKEND / "api" / "routes" / "status.py").read_text(encoding="utf-8")
_NOTIF_ROUTE_SRC = (BACKEND / "api" / "routes" / "notifications.py").read_text(encoding="utf-8")
_MAIN_SRC = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
_ADMIN_SRC = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
_MIGRATION_SRC = (BACKEND / "db" / "038_status_events_notifications.sql").read_text(encoding="utf-8")
_EVENTS_SRC = (_CORE_DIR / "events.py").read_text(encoding="utf-8")


class TestCoreFastAPIFree:
    """S3: core/status/ は FastAPI / LLM クライアントを import しない。"""

    def test_no_fastapi_import_in_core(self):
        for path in _CORE_DIR.rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            assert "import fastapi" not in src, f"{path} が fastapi を import している"
            assert "from fastapi" not in src, f"{path} が fastapi を import している"

    def test_no_llm_client_import_in_core(self):
        for path in _CORE_DIR.rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            assert "core.llm" not in src, f"{path} が core.llm を import している（S3: 非LLM）"


class TestWatermarkIdempotency:
    """S2: 遷移は append-only の事実。冪等（UNIQUE 制約 + ON CONFLICT DO NOTHING）で二重発火しない。"""

    def test_unique_constraint_in_migration(self):
        assert "UNIQUE (source_table, source_id, event_kind)" in _MIGRATION_SRC

    def test_watcher_uses_on_conflict_do_nothing(self):
        assert "ON CONFLICT (source_table, source_id, event_kind) DO NOTHING" in _WATCHER_SRC

    def test_watcher_uses_watermark_scan(self):
        assert "MAX(occurred_at)" in _WATCHER_SRC
        assert "status_events" in _WATCHER_SRC

    def test_watcher_uses_thread_and_env_flag(self):
        assert "threading.Thread" in _WATCHER_SRC
        assert "STATUS_WATCH_ENABLED" in _WATCHER_SRC

    def test_watcher_only_scans_terminal_states(self):
        assert "'completed', 'failed'" in _WATCHER_SRC


class TestNotificationRecipientsScoped:
    """S5: 通知の宛先はサーバ側で権限判定（所有者・共有 editor のみ）に限る。"""

    def test_material_recipients_owner_and_editor_only(self):
        assert "uploaded_by" in _NOTIF_RULES_SRC
        assert "permission = 'editor'" in _NOTIF_RULES_SRC
        assert "'viewer'" not in _NOTIF_RULES_SRC  # viewer には通知しない

    def test_course_recipients_owner_and_editor_only(self):
        assert "SELECT user_id::text AS uid FROM learning_courses" in _NOTIF_RULES_SRC

    def test_fan_out_rejects_unknown_event_kind(self):
        assert "EVENT_TO_NOTIFICATION_KIND.get(event_kind)" in _NOTIF_RULES_SRC


class TestNoRowDeletion:
    """S4: 既読・却下は状態遷移で行い、行削除しない。"""

    def test_inbox_has_no_delete_statement(self):
        assert "DELETE FROM user_notifications" not in _INBOX_SRC
        assert "DELETE FROM" not in _INBOX_SRC

    def test_mark_read_sets_column_not_delete(self):
        assert "SET read_at = now()" in _INBOX_SRC

    def test_dismiss_sets_column_not_delete(self):
        assert "SET dismissed_at = now()" in _INBOX_SRC

    def test_notification_route_exposes_dismiss(self):
        assert '"/{notification_id}/dismiss"' in _NOTIF_ROUTE_SRC


class TestFailClosedUnknown:
    """S5: 投影不能なエンティティは unknown を返す（例外にしない）。"""

    def test_material_state_unknown_constant_exists(self):
        assert 'MATERIAL_STATE_UNKNOWN = "unknown"' in _SCHEMA_SRC

    def test_projector_returns_unknown_for_missing_document(self):
        idx = _PROJECTOR_SRC.find("def project_material_status")
        assert idx != -1
        body = _PROJECTOR_SRC[idx:idx + 800]
        assert "if not doc:" in body
        assert "MATERIAL_STATE_UNKNOWN" in body

    def test_projector_returns_unregistered_for_missing_course(self):
        idx = _PROJECTOR_SRC.find("def project_course_status")
        assert idx != -1
        body = _PROJECTOR_SRC[idx:idx + 500]
        assert "if not course:" in body
        assert "registered=False" in body


class TestNotificationKindCapAtSix:
    """v1 の通知 kind は完了・失敗の6種に限定する（段階登録の構造的強制）。"""

    def test_notification_kinds_tuple_has_exactly_six(self):
        from core.status import schema as sschema
        assert len(sschema.NOTIFICATION_KINDS) == 6

    def test_event_to_notification_mapping_covers_exactly_notification_kinds(self):
        from core.status import schema as sschema
        assert set(sschema.EVENT_TO_NOTIFICATION_KIND.values()) == set(sschema.NOTIFICATION_KINDS)


class TestRouterRegistration:
    def test_status_router_mounted_under_admin(self):
        assert "from routes.status import router as _status_router" in _ADMIN_SRC
        assert "router.include_router(_status_router)" in _ADMIN_SRC

    def test_notifications_router_mounted_under_admin(self):
        assert "from routes.notifications import router as _notifications_router" in _ADMIN_SRC
        assert "router.include_router(_notifications_router)" in _ADMIN_SRC

    def test_watcher_wired_in_lifespan(self):
        assert "start_watcher" in _MAIN_SRC


class TestStatusRoutesRequireTeacherAndOwnership:
    def test_all_endpoints_require_teacher(self):
        assert _STATUS_ROUTE_SRC.count("Depends(_require_teacher)") >= 4

    def test_single_resource_endpoints_check_ownership(self):
        assert "user_owns_document(" in _STATUS_ROUTE_SRC
        assert "user_owns_course(" in _STATUS_ROUTE_SRC

    def test_events_endpoint_present_and_scoped(self):
        assert '"/events"' in _STATUS_ROUTE_SRC
        assert "422" in _STATUS_ROUTE_SRC


class TestEventsReader:
    """core/status/events.py: status_events の読み取り専用アクセサ（Phase 3）。

    書き込み API を作らない・ORDER BY / LIMIT clamp を持つことを構造的に検証する
    （実 DB を使わない構造テスト。core/status/*.py への FastAPI/LLM 非import は
    既存の rglob ループが自動的にカバーする）。
    """

    def test_events_module_exists(self):
        assert (_CORE_DIR / "events.py").exists()

    def test_orders_by_occurred_at_desc(self):
        assert "ORDER BY occurred_at DESC" in _EVENTS_SRC

    def test_limit_is_clamped(self):
        assert "_MIN_LIMIT" in _EVENTS_SRC
        assert "_MAX_LIMIT" in _EVENTS_SRC
        assert "min(_MAX_LIMIT" in _EVENTS_SRC or "min(" in _EVENTS_SRC

    def test_no_write_statements(self):
        for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
            assert forbidden not in _EVENTS_SRC


class TestSharedLayerUntouched:
    """既存 V層（share_notifications / core/versioning）は読み取りのみで変更しない。"""

    def test_unified_route_only_imports_versioning_reads(self):
        assert "from core.versioning import notifications as shared_notifications" in _NOTIF_ROUTE_SRC
        for forbidden in ("INSERT INTO share_notifications", "DELETE FROM share_notifications",
                          "ALTER TABLE share_notifications"):
            assert forbidden not in _NOTIF_ROUTE_SRC


_CAN_IMPORT_SCHEMA = True
try:  # pragma: no cover - 依存が無い CI-lite では skip
    from core.status import schema as _sschema
except Exception:  # noqa: BLE001
    _CAN_IMPORT_SCHEMA = False


@pytest.mark.skipif(not _CAN_IMPORT_SCHEMA, reason="core.status の依存（sqlalchemy 等）未導入")
class TestSchemaLogic:
    def test_notification_kinds_count(self):
        assert len(_sschema.NOTIFICATION_KINDS) == 6

    def test_event_kinds_count(self):
        assert len(_sschema.EVENT_KINDS) == 6

    def test_material_status_to_dict(self):
        status = _sschema.MaterialStatus(document_id="d1", material_id="m1", state=_sschema.MATERIAL_STATE_UNKNOWN)
        d = status.to_dict()
        assert d["state"] == "unknown"

    def test_course_status_to_dict_defaults(self):
        status = _sschema.CourseStatus(course_id="c1")
        d = status.to_dict()
        assert d["script_status"] == _sschema.SCRIPT_STATUS_DRAFT
        assert d["audio_status"] == _sschema.AUDIO_STATUS_NONE
