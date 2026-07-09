"""V層のガードレール（構造テスト・実 DB / FastAPI 不要）。

- core/versioning/ は FastAPI を import しない（テスタビリティ・project rule 2）
- 発行・削除予約・取消は所有者ガード（_require_owner）
- purge は既存削除相当 + orphan gap 解消（document_analysis_runs / theory_* を消す）
- 削除スイーパは threading + 環境フラグでガード
- 監査 entity_type / 通知種別 / 既定猶予日数の語彙
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_CORE_DIR = BACKEND / "core" / "versioning"
_ROUTE_SRC = (BACKEND / "api" / "routes" / "versioning.py").read_text(encoding="utf-8")
_MAIN_SRC = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
_ADMIN_SRC = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
_DELETION_SRC = (_CORE_DIR / "deletion.py").read_text(encoding="utf-8")
_WORKER_SRC = (_CORE_DIR / "worker.py").read_text(encoding="utf-8")
_SCHEMA_SRC = (_CORE_DIR / "schema.py").read_text(encoding="utf-8")
_AUDIT_SRC = (_CORE_DIR / "audit.py").read_text(encoding="utf-8")
_NOTIF_SRC = (_CORE_DIR / "notifications.py").read_text(encoding="utf-8")
_SERVICES_SRC = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
_FRONTEND_JS = BACKEND.parent / "frontend" / "public" / "js"
_VERSIONING_JS = (_FRONTEND_JS / "versioning.js").read_text(encoding="utf-8")
_APP_JS = (_FRONTEND_JS / "app.js").read_text(encoding="utf-8")


class TestCourseVersionViewCentralized:
    """Issue 1: 学習者の全読み取り経路が同一の版解決を通り、経路差の不整合が出ないこと。"""

    def test_shared_helper_used_by_all_read_paths(self):
        # get_course_data / get_viewable_course_data / get_accessible_course_data(public+group)
        assert "def _apply_course_version_view" in _SERVICES_SRC
        assert _SERVICES_SRC.count("_apply_course_version_view(course_id, user_id") >= 4

    def test_learner_primary_path_is_version_resolved(self):
        # 学習者の主経路 get_course_data も版解決を通す（旧実装は live HEAD 直返しだった）
        idx = _SERVICES_SRC.find("def get_course_data(")
        end = _SERVICES_SRC.find("def get_personal_layer(")
        body = _SERVICES_SRC[idx:end]
        assert "_apply_course_version_view(course_id, user_id, data)" in body

    def test_editor_reads_head_in_shared_helper(self):
        # owner/editor は helper 内で HEAD（live）を読む（get_accessible の editor 免除ズレを解消）
        assert "user_can_edit_course(viewer_id, course_id)" in _SERVICES_SRC


class TestManualDeleteTeardown:
    """Issue 2b: 既存の即時削除も V層状態を掃除し購読者へ通知する（幽霊ピン・陳腐化通知を残さない）。"""

    def test_deletion_module_exposes_teardown(self):
        assert "def teardown_versioning" in _DELETION_SRC
        assert "def _cleanup_version_tables" in _DELETION_SRC

    def test_admin_delete_endpoints_call_teardown(self):
        # delete_material と delete_course の両方が teardown を呼ぶ
        assert _ADMIN_SRC.count("_versioning_teardown_after_delete(") >= 2
        assert "_versioning_collect_recipients(" in _ADMIN_SRC

    def test_purge_document_cleans_doubt_layer_orphans(self):
        # Issue 2c: D層の FK-less polymorphic 行の孤児を掃除する
        for tbl in ("epistemic_ledger", "challenges", "counterfactual_sessions"):
            assert tbl in _DELETION_SRC, tbl


class TestNotificationRecipientsLegacyGroup:
    """Issue 2c: レガシー単一グループ共有（documents.visibility='group'）の閲覧者も通知宛先に含む。"""

    def test_recipients_unified_source_of_truth(self):
        assert "def _recipient_ids" in _NOTIF_SRC

    def test_document_recipients_include_legacy_group_visibility(self):
        assert "d.visibility = 'group'" in _NOTIF_SRC


class TestSourceDocumentDeletionNotice:
    """Issue 2a: 元教材の削除予約も学習者バナーで警告する（教材 purge は所有者コースを巻き添え削除）。"""

    def test_services_exposes_course_deletion_notice(self):
        assert "def course_deletion_notice" in _SERVICES_SRC

    def test_learner_notice_route_uses_helper(self):
        learning_src = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        assert "course_deletion_notice(course_id)" in learning_src


class TestDocumentUIHonest:
    """Issue 3: 教材(document)の版固定ブラウズは未対応 → UI で過剰な約束をしない。"""

    def test_document_intro_does_not_overpromise(self):
        assert "_introText" in _VERSIONING_JS
        # 「同意するまで自分の版に留まる」の約束は course 限定。document は未対応を明記する。
        assert "今後対応予定" in _VERSIONING_JS

    def test_notification_note_not_double_escaped(self):
        # _notifText は生テキストを返し、_renderNotif の esc() で一度だけエスケープする
        assert "esc(_notifText(n))" in _VERSIONING_JS
        assert "esc(n.payload.note)" not in _VERSIONING_JS


class TestLearnerBannerRobust:
    """Issue 2a UX: 猶予バナーの文法崩れ回避 + 画面離脱時の残留除去。"""

    def test_banner_cleanup_helper_defined_and_called(self):
        assert "function removeVersionNoticeBanner" in _APP_JS
        # 定義 + showNoCourseState + renderAuth の3箇所
        assert _APP_JS.count("removeVersionNoticeBanner") >= 3

    def test_banner_anchored_to_main_column(self):
        assert 'querySelector(".mn")' in _APP_JS


class TestCoreFastAPIFree:
    def test_no_fastapi_import_in_core(self):
        for path in _CORE_DIR.rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            assert "import fastapi" not in src, f"{path} が fastapi を import している"
            assert "from fastapi" not in src, f"{path} が fastapi を import している"


class TestOwnerGuards:
    def test_publish_and_deletion_owner_guarded(self):
        # publish / schedule_deletion / cancel_deletion の3つは所有者限定
        assert _ROUTE_SRC.count("_require_owner(") >= 3

    def test_adopt_and_reads_viewer_guarded(self):
        assert "_require_viewer(" in _ROUTE_SRC

    def test_non_owner_maps_to_404(self):
        # 既存 user_owns_* の慣習: 非所有者は 404
        assert "status_code=404" in _ROUTE_SRC

    def test_error_mapping_covers_states(self):
        assert "410" in _ROUTE_SRC   # PurgedError
        assert "409" in _ROUTE_SRC   # PendingDeletion / AdoptConflict
        assert "422" in _ROUTE_SRC   # VersioningError


class TestPurgeClosesOrphanGap:
    def test_purge_deletes_analysis_runs(self):
        assert "DELETE FROM document_analysis_runs" in _DELETION_SRC

    def test_purge_deletes_document_scoped_theory_tables(self):
        for tbl in ("theory_claims", "theory_components", "theory_component_graphs", "theory_component_links"):
            assert tbl in _DELETION_SRC, tbl

    def test_purge_cleans_versioning_tables(self):
        for tbl in ("shared_versions", "shared_version_subscriptions", "share_notifications"):
            assert f"DELETE FROM {tbl}" in _DELETION_SRC, tbl

    def test_purge_leaves_purged_tombstone(self):
        assert "'purged'" in _DELETION_SRC

    def test_purge_idempotent_guard(self):
        assert "already_purged" in _DELETION_SRC


class TestSweeper:
    def test_uses_thread_and_env_flag(self):
        assert "threading.Thread" in _WORKER_SRC
        assert "VERSION_SWEEPER_ENABLED" in _WORKER_SRC

    def test_only_purges_due_pending(self):
        assert "pending_deletion" in _WORKER_SRC
        assert "delete_purge_after <= now()" in _WORKER_SRC

    def test_notifies_before_permissions_gone(self):
        # 宛先収集は purge の前（権限が消える前）に行う
        idx_recips = _WORKER_SRC.find("recipients_for")
        idx_purge = _WORKER_SRC.find("purge_object")
        assert idx_recips != -1 and idx_purge != -1 and idx_recips < idx_purge


class TestAuditVocabulary:
    def test_audit_entity_types(self):
        for t in ("shared_release", "shared_deletion", "shared_subscription"):
            assert t in _SCHEMA_SRC, t

    def test_audit_uses_review_events_table(self):
        assert "theory_review_events" in _AUDIT_SRC

    def test_notification_kinds(self):
        for k in ("version_published", "deletion_scheduled", "deletion_cancelled", "deleted"):
            assert k in _SCHEMA_SRC, k

    def test_default_grace_is_14(self):
        assert "DEFAULT_GRACE_DAYS = 14" in _SCHEMA_SRC


class TestRouterRegistration:
    def test_router_mounted_under_admin(self):
        # versioning ルータは admin ルータ配下（/api/admin）にマウントする
        assert "from routes.versioning import router as _versioning_router" in _ADMIN_SRC
        assert "router.include_router(_versioning_router)" in _ADMIN_SRC

    def test_sweeper_wired_in_lifespan(self):
        assert "start_background_sweeper" in _MAIN_SRC


_CAN_IMPORT_SCHEMA = True
try:  # pragma: no cover - 依存が無い CI-lite では skip
    from core.versioning import schema as _vschema
except Exception:  # noqa: BLE001
    _CAN_IMPORT_SCHEMA = False


@pytest.mark.skipif(not _CAN_IMPORT_SCHEMA, reason="core.versioning の依存（sqlalchemy 等）未導入")
class TestSchemaLogic:
    def test_object_type_validation(self):
        assert _vschema.is_valid_object_type("course")
        assert _vschema.is_valid_object_type("document")
        assert not _vschema.is_valid_object_type("widget")

    def test_grace_default(self):
        assert _vschema.DEFAULT_GRACE_DAYS == 14

    def test_error_hierarchy(self):
        assert issubclass(_vschema.PurgedError, _vschema.VersioningError)
        assert issubclass(_vschema.AdoptConflictError, _vschema.VersioningError)
