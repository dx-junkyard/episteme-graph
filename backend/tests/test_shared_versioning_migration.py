"""V層（migration 037）スキーマの静的検査。

実 DB なしで、参照 SQL と main.py インライン適用の整合を確認する
（test_course_group_permissions.py / test_endorsement_sharing.py と同型）。
"""
from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
_SQL = (BACKEND / "db" / "037_shared_versioning.sql").read_text(encoding="utf-8")
_MAIN = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")


class TestReferenceSQL:
    def test_reference_file_exists(self):
        assert (BACKEND / "db" / "037_shared_versioning.sql").exists()

    def test_defines_four_tables(self):
        for tbl in (
            "shared_versions",
            "shared_version_state",
            "shared_version_subscriptions",
            "share_notifications",
        ):
            assert f"CREATE TABLE IF NOT EXISTS {tbl}" in _SQL, tbl

    def test_release_unique_version_per_object(self):
        assert "UNIQUE (object_type, object_id, version_no)" in _SQL

    def test_object_type_polymorphic_no_fk(self):
        # ポリモーフィック（course=TEXT / document=UUID）なので object_id は TEXT かつ FK なし
        import re
        assert re.search(r"object_id\s+TEXT NOT NULL", _SQL)
        assert "CHECK (object_type IN ('course', 'document'))" in _SQL
        # object_id への FK 定義が無いこと
        assert "object_id" in _SQL and "REFERENCES documents" not in _SQL.split("shared_version_subscriptions")[0]

    def test_lifecycle_check(self):
        assert "lifecycle IN ('active', 'pending_deletion', 'purged')" in _SQL

    def test_notification_kind_check(self):
        for kind in ("version_published", "deletion_scheduled", "deletion_cancelled", "deleted"):
            assert kind in _SQL, kind

    def test_pending_purge_partial_index(self):
        assert "idx_svs_pending_purge" in _SQL
        assert "WHERE lifecycle = 'pending_deletion'" in _SQL

    def test_subscription_unique_per_subscriber(self):
        assert "UNIQUE (object_type, object_id, subscriber_id)" in _SQL

    def test_citation_version_pinning_columns(self):
        assert "ALTER TABLE component_citations" in _SQL
        for col in ("source_object_type", "source_object_id", "source_release_id", "source_version_no"):
            assert col in _SQL, col


class TestAppliedInMain:
    def test_migration_037_block_present(self):
        assert "Migration 037" in _MAIN
        assert "CREATE TABLE IF NOT EXISTS shared_versions" in _MAIN
        assert "CREATE TABLE IF NOT EXISTS share_notifications" in _MAIN

    def test_success_log_bumped(self):
        # migration 038 が後続で log 行を (002-038) へ更新済み（本行は最終値を主張しない）。
        assert "Migrations (002-036) applied successfully." not in _MAIN

    def test_citation_columns_applied_in_main(self):
        assert "ADD COLUMN IF NOT EXISTS source_release_id" in _MAIN

    def test_sweeper_started_in_lifespan(self):
        assert "start_background_sweeper" in _MAIN
        assert "core.versioning" in _MAIN
