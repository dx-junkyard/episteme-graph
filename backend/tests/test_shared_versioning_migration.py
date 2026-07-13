"""V層（migration 037）+ 通知テーブル統合（migration 045, Tier 3-15）スキーマの静的検査。

実 DB なしで、正本 SQL（backend/db/037_shared_versioning.sql /
backend/db/045_unified_notifications.sql）の DDL 構造を確認する
（migration DDL の main.py インラインとの二重整合検査は 2026-07 の migration 正本
一本化により廃止。main.py 側は lifespan でのワーカー起動検査のみ残す）。

share_notifications（037 が元々定義していた通知インボックス）は migration 045 で
user_notifications（migration 038, 状態管理・通知基盤）に統合されたため、037 は
もう当該テーブルを CREATE しない（統合済み no-op スタブ、010/035 と同じパターン）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
_SQL_037 = (BACKEND / "db" / "037_shared_versioning.sql").read_text(encoding="utf-8")
_SQL_045 = (BACKEND / "db" / "045_unified_notifications.sql").read_text(encoding="utf-8")
_MAIN = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")


class TestReferenceSQL:
    def test_reference_file_exists(self):
        assert (BACKEND / "db" / "037_shared_versioning.sql").exists()

    def test_defines_three_tables(self):
        # share_notifications は migration 045 で user_notifications に統合済み
        # （本ファイルはもう CREATE しない）。
        for tbl in (
            "shared_versions",
            "shared_version_state",
            "shared_version_subscriptions",
        ):
            assert f"CREATE TABLE IF NOT EXISTS {tbl}" in _SQL_037, tbl

    def test_share_notifications_stubbed_out_of_037(self):
        assert "CREATE TABLE IF NOT EXISTS share_notifications" not in _SQL_037
        assert "migration 045" in _SQL_037

    def test_release_unique_version_per_object(self):
        assert "UNIQUE (object_type, object_id, version_no)" in _SQL_037

    def test_object_type_polymorphic_no_fk(self):
        # ポリモーフィック（course=TEXT / document=UUID）なので object_id は TEXT かつ FK なし
        import re
        assert re.search(r"object_id\s+TEXT NOT NULL", _SQL_037)
        assert "CHECK (object_type IN ('course', 'document'))" in _SQL_037
        # object_id への FK 定義が無いこと
        assert "object_id" in _SQL_037 and "REFERENCES documents" not in _SQL_037.split("shared_version_subscriptions")[0]

    def test_lifecycle_check(self):
        assert "lifecycle IN ('active', 'pending_deletion', 'purged')" in _SQL_037

    def test_pending_purge_partial_index(self):
        assert "idx_svs_pending_purge" in _SQL_037
        assert "WHERE lifecycle = 'pending_deletion'" in _SQL_037

    def test_subscription_unique_per_subscriber(self):
        assert "UNIQUE (object_type, object_id, subscriber_id)" in _SQL_037

    def test_citation_version_pinning_columns(self):
        assert "ALTER TABLE component_citations" in _SQL_037
        for col in ("source_object_type", "source_object_id", "source_release_id", "source_version_no"):
            assert f"ADD COLUMN IF NOT EXISTS {col}" in _SQL_037, col


class TestUnifiedNotificationsMigration:
    """migration 045: share_notifications → user_notifications 統合のスキーマ検査。"""

    def test_reference_file_exists(self):
        assert (BACKEND / "db" / "045_unified_notifications.sql").exists()

    def test_adds_three_columns(self):
        for col in ("source", "release_id", "acted_at"):
            assert f"ADD COLUMN IF NOT EXISTS {col}" in _SQL_045, col

    def test_source_check_constraint(self):
        assert "CHECK (source IN ('status', 'shared'))" in _SQL_045

    def test_source_default_is_status(self):
        # 既存 status 層行は ALTER 時点で全て source='status' になる（既定値）。
        assert "DEFAULT 'status'" in _SQL_045

    def test_release_id_references_shared_versions(self):
        assert "REFERENCES shared_versions(id)" in _SQL_045

    def test_no_kind_check_constraint(self):
        # open-vocab 設計: kind の語彙ゲートは DB CHECK ではなく Python 側
        # (core/status/schema.py / core/versioning/schema.py) が正本。
        assert "CHECK (kind IN" not in _SQL_045

    def test_data_migration_guarded_by_existence_check(self):
        assert "information_schema.tables" in _SQL_045
        assert "table_name = 'share_notifications'" in _SQL_045

    def test_data_migration_inserts_with_conflict_guard(self):
        assert "INSERT INTO user_notifications" in _SQL_045
        assert "ON CONFLICT (id) DO NOTHING" in _SQL_045
        assert "SELECT id, recipient_id, kind, object_type, object_id, release_id" in _SQL_045
        assert "'shared'" in _SQL_045

    def test_drops_old_table(self):
        assert "DROP TABLE share_notifications" in _SQL_045


_CAN_IMPORT_SCHEMA = True
try:  # pragma: no cover - 依存が無い CI-lite では skip
    from core.versioning import schema as _vschema
except Exception:  # noqa: BLE001
    _CAN_IMPORT_SCHEMA = False


@pytest.mark.skipif(not _CAN_IMPORT_SCHEMA, reason="core.versioning の依存（sqlalchemy 等）未導入")
class TestNotificationKindVocabularyLivesInPython:
    """kind の語彙ゲートは DB CHECK ではなく core/versioning/schema.py が正本。"""

    def test_kind_vocabulary(self):
        for kind in ("version_published", "deletion_scheduled", "deletion_cancelled", "deleted"):
            assert kind in _vschema.NOTIFICATION_KINDS, kind


class TestLifespanWiring:
    """migration DDL 以外の main.py 検査（lifespan でのワーカー起動）はそのまま維持する。"""

    def test_sweeper_started_in_lifespan(self):
        assert "start_background_sweeper" in _MAIN
        assert "core.versioning" in _MAIN
