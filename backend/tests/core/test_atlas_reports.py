"""分野の地図 — 修正報告 (Issue D) core.atlas_reports のユニットテスト。

DB 接続なしで検証できるよう、純粋関数と FakeSession による SQL 実行検証で構成する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import atlas_reports  # noqa: E402
from core.atlas import IdMigration  # noqa: E402


# ---------------------------------------------------------------------------
# FakeSession
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    """execute() の SQL とパラメータを記録し、ルーティング表で応答を返す。"""

    def __init__(self, router=None):
        self.calls = []  # (sql, params)
        self.router = router or (lambda sql, params: FakeResult())
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, params or {}))
        return self.router(sql, params or {})

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _report(**overrides) -> dict:
    base = {
        "id": "r1",
        "cartridge_id": "particle_physics",
        "skeleton_version": "2026.1",
        "node_id": "dual",
        "region_id": "",
        "level": 1,
        "node_label": "双対性の仮定",
        "report_text": "配置が実際と違う",
        "reporter_id": "u-1",
        "reporter_name": "hanako",
        "status": atlas_reports.STATUS_PENDING,
        "resolution_note": "",
        "resolved_by": None,
        "resolved_at": None,
        "merged_into": None,
        "applied_version": "",
        "notified_at": None,
        "created_at": "2026-07-01T00:00:00",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 入力検証 (D-1: 空文字ガード・自動添付メタの必須化)
# ---------------------------------------------------------------------------


class TestValidateReportInput:
    def test_valid_input_has_no_errors(self):
        errors = atlas_reports.validate_report_input(
            text="ここが違う", node_id="dual", region_id="", level=1,
            skeleton_version="2026.1",
        )
        assert errors == []

    def test_empty_text_is_rejected(self):
        """空文字送信のガード(「(内容未記入)」は本実装では送信不可)。"""
        errors = atlas_reports.validate_report_input(
            text="   ", node_id="dual", region_id="", level=1,
            skeleton_version="2026.1",
        )
        assert any("空" in e for e in errors)

    def test_missing_target_is_rejected(self):
        errors = atlas_reports.validate_report_input(
            text="x", node_id="", region_id="", level=1, skeleton_version="2026.1"
        )
        assert any("対象" in e for e in errors)

    def test_region_only_target_is_accepted(self):
        errors = atlas_reports.validate_report_input(
            text="x", node_id="", region_id="fog_lattice", level=1,
            skeleton_version="2026.1",
        )
        assert errors == []

    def test_missing_skeleton_version_is_rejected(self):
        """どの版への指摘かを固定するため skeleton_version は必須。"""
        errors = atlas_reports.validate_report_input(
            text="x", node_id="dual", region_id="", level=1, skeleton_version=""
        )
        assert any("skeleton_version" in e for e in errors)

    def test_invalid_level_is_rejected(self):
        errors = atlas_reports.validate_report_input(
            text="x", node_id="dual", region_id="", level=9,
            skeleton_version="2026.1",
        )
        assert any("level" in e for e in errors)


# ---------------------------------------------------------------------------
# キュー整形 (D-2: 旧版識別・件数・改版検討ヒント)
# ---------------------------------------------------------------------------


class TestSummarizeQueue:
    def test_version_mismatch_is_flagged(self):
        """旧版への報告がレビュー画面で識別できる (受け入れ条件5)。"""
        reports = [
            _report(id="r1", skeleton_version="2025.9"),
            _report(id="r2", skeleton_version="2026.1"),
        ]
        summary = atlas_reports.summarize_queue(reports, "2026.1")
        by_id = {r["id"]: r for r in summary["reports"]}
        assert by_id["r1"]["version_mismatch"] is True
        assert by_id["r2"]["version_mismatch"] is False

    def test_no_current_version_means_no_mismatch(self):
        summary = atlas_reports.summarize_queue([_report()], "")
        assert summary["reports"][0]["version_mismatch"] is False

    def test_open_counts_accumulate_per_target(self):
        """同一ノードへの報告蓄積の件数 — 改版トリガ判断の材料 (D-2)。"""
        reports = [
            _report(id="r1"),
            _report(id="r2", status=atlas_reports.STATUS_ACCEPTED),
            _report(id="r3", status=atlas_reports.STATUS_DECLINED),  # クローズ済みは数えない
            _report(id="r4", status=atlas_reports.STATUS_ACCEPTED, applied_version="2026.1"),
            _report(id="r5", node_id="", region_id="fog_lattice"),
        ]
        summary = atlas_reports.summarize_queue(reports, "2026.1")
        assert summary["target_counts"]["dual"] == 2  # pending + 未反映 accepted
        assert summary["target_counts"]["fog_lattice"] == 1

    def test_revision_hint_appears_at_threshold(self):
        """未決事項の決定: 閾値 (5件) 以上で改版検討ヒントを出す (自動改版はしない)。"""
        threshold = atlas_reports.REVISION_TRIGGER_THRESHOLD
        reports = [_report(id=f"r{i}") for i in range(threshold)]
        summary = atlas_reports.summarize_queue(reports, "2026.1")
        assert summary["revision_hint_targets"] == ["dual"]
        assert summary["revision_trigger_threshold"] == threshold

        summary = atlas_reports.summarize_queue(reports[:-1], "2026.1")
        assert summary["revision_hint_targets"] == []


class TestCreditsAndMigrations:
    def test_credits_are_deduplicated_and_ordered(self):
        reports = [
            _report(reporter_name="hanako"),
            _report(reporter_name="taro"),
            _report(reporter_name="hanako"),
            _report(reporter_name="", reporter_id="u-9"),
        ]
        assert atlas_reports.credits_from_reports(reports) == ["hanako", "taro", "u-9"]

    def test_migration_map_filters_by_version(self):
        migrations = (
            IdMigration(from_id="old_a", to_id="new_a", version="2027.1"),
            IdMigration(from_id="old_b", to_id="new_b", version="2026.1"),
        )
        mapping = atlas_reports.migration_map_for_version(migrations, "2027.1")
        assert mapping == {"old_a": "new_a"}

    def test_migration_map_accepts_dict_entries(self):
        mapping = atlas_reports.migration_map_for_version(
            [{"from": "x", "to": "y", "version": "2027.1"}], "2027.1"
        )
        assert mapping == {"x": "y"}


# ---------------------------------------------------------------------------
# DB 操作 (FakeSession)
# ---------------------------------------------------------------------------


class TestCreateReport:
    def test_insert_carries_attribution_and_version(self):
        session = FakeSession(lambda sql, p: FakeResult(rows=[("new-id",)]))
        report_id = atlas_reports.create_report(
            session,
            cartridge_id="particle_physics",
            skeleton_version="2026.1",
            node_id="dual",
            region_id="",
            level=1,
            node_label="双対性の仮定",
            text="  配置が違う  ",
            reporter_id="user-uuid",
        )
        assert report_id == "new-id"
        sql, params = session.calls[0]
        assert "INSERT INTO atlas_correction_reports" in sql
        assert params["reporter_id"] == "user-uuid"  # 帰属つき (匿名不可)
        assert params["skeleton_version"] == "2026.1"
        assert params["report_text"] == "配置が違う"
        assert params["kind"] == atlas_reports.REPORT_KIND


class TestResolveReport:
    def _session_with_status(self, status):
        return FakeSession(
            lambda sql, p: FakeResult(rows=[(status,)]) if sql.startswith("SELECT") else FakeResult(rowcount=1)
        )

    def test_unknown_action_raises(self):
        with pytest.raises(ValueError):
            atlas_reports.resolve_report(
                FakeSession(), report_id="r1", action="nuke", note="", resolver_id="t1"
            )

    def test_decline_requires_note(self):
        """見送りは理由つき (D-2)。"""
        with pytest.raises(ValueError, match="理由"):
            atlas_reports.resolve_report(
                self._session_with_status("pending"),
                report_id="r1", action="decline", note="  ", resolver_id="t1",
            )

    def test_merge_requires_target(self):
        with pytest.raises(ValueError, match="merge_into"):
            atlas_reports.resolve_report(
                self._session_with_status("pending"),
                report_id="r1", action="merge", note="", resolver_id="t1",
            )

    def test_merge_into_self_is_rejected(self):
        with pytest.raises(ValueError, match="自分自身"):
            atlas_reports.resolve_report(
                self._session_with_status("pending"),
                report_id="r1", action="merge", note="", resolver_id="t1",
                merge_into="r1",
            )

    def test_missing_report_raises_lookup_error(self):
        session = FakeSession(lambda sql, p: FakeResult(rows=[]))
        with pytest.raises(LookupError):
            atlas_reports.resolve_report(
                session, report_id="nope", action="accept", note="", resolver_id="t1"
            )

    def test_accept_updates_status_and_returns_transition(self):
        session = self._session_with_status("pending")
        transition = atlas_reports.resolve_report(
            session, report_id="r1", action="accept", note="良い指摘", resolver_id="t1"
        )
        assert transition == {"old_status": "pending", "new_status": "accepted"}
        update_sql, update_params = session.calls[-1]
        assert "UPDATE atlas_correction_reports" in update_sql
        assert update_params["status"] == "accepted"
        # 処理結果は報告者への通知対象になる (notified_at を未読へ)
        assert "notified_at = NULL" in update_sql


class TestAckAndFreezeApplication:
    def test_ack_only_matches_own_resolved_reports(self):
        session = FakeSession(lambda sql, p: FakeResult(rowcount=0))
        assert atlas_reports.ack_report(session, report_id="r1", user_id="u1") is False
        sql, params = session.calls[0]
        assert "reporter_id = CAST(:user_id AS uuid)" in sql
        assert "resolved_at IS NOT NULL" in sql

    def test_apply_freeze_stamps_accepted_and_migrates_pending(self):
        """凍結時: 採用済みは applied_version 刻印で自動クローズ、
        pending は id_migrations に従って新版へ引き継ぐ (D-3)。"""
        session = FakeSession(lambda sql, p: FakeResult(rowcount=2))
        migrations = (IdMigration(from_id="old_a", to_id="new_a", version="2027.1"),)
        summary = atlas_reports.apply_freeze_to_reports(
            session,
            cartridge_id="particle_physics",
            version="2027.1",
            id_migrations=migrations,
        )
        assert summary == {"applied": 2, "migrated": 2}
        stamp_sql, stamp_params = session.calls[0]
        assert "SET applied_version = :version" in stamp_sql
        assert stamp_params["status"] == atlas_reports.STATUS_ACCEPTED
        migrate_sql, migrate_params = session.calls[1]
        assert "SET node_id = :to_id" in migrate_sql
        assert migrate_params == {
            "cartridge_id": "particle_physics",
            "to_id": "new_a",
            "from_id": "old_a",
            "status": atlas_reports.STATUS_PENDING,
        }

    def test_apply_freeze_ignores_other_version_migrations(self):
        session = FakeSession(lambda sql, p: FakeResult(rowcount=0))
        migrations = (IdMigration(from_id="a", to_id="b", version="2026.1"),)
        summary = atlas_reports.apply_freeze_to_reports(
            session, cartridge_id="c", version="2027.1", id_migrations=migrations
        )
        assert summary == {"applied": 0, "migrated": 0}
        assert len(session.calls) == 1  # 刻印のみ。migration の UPDATE は走らない
