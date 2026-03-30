"""reextractor モジュールの単体テスト。

DB呼び出しをモック化してジョブ管理ロジックをテストする。
外部 API は一切呼び出さない。
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestGetJobStatus:
    """get_job_status のテスト。"""

    @patch("core.reextractor._pg_session")
    def test_returns_none_for_missing_job(self, mock_pg):
        """存在しないジョブIDの場合はNoneを返す。"""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = None
        mock_pg.return_value = mock_session

        from core.reextractor import get_job_status
        result = get_job_status("nonexistent")
        assert result is None

    @patch("core.reextractor._pg_session")
    def test_returns_dict_for_existing_job(self, mock_pg):
        """存在するジョブの場合は正しいdict構造を返す。"""
        now = datetime.now(timezone.utc)
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchone.return_value = (
            "job-1", "prop-1", "running", 10, 3, None, now, None, now,
        )
        mock_pg.return_value = mock_session

        from core.reextractor import get_job_status
        result = get_job_status("job-1")

        assert result is not None
        assert result["job_id"] == "job-1"
        assert result["proposal_id"] == "prop-1"
        assert result["status"] == "running"
        assert result["total_docs"] == 10
        assert result["processed_docs"] == 3
        assert result["error_message"] is None


class TestGetJobs:
    """get_jobs のテスト。"""

    @patch("core.reextractor._pg_session")
    def test_returns_empty_list_when_no_jobs(self, mock_pg):
        """ジョブがない場合は空リストを返す。"""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = []
        mock_pg.return_value = mock_session

        from core.reextractor import get_jobs
        result = get_jobs()
        assert result == []

    @patch("core.reextractor._pg_session")
    def test_returns_list_of_dicts(self, mock_pg):
        """ジョブがある場合は正しいdict構造のリストを返す。"""
        now = datetime.now(timezone.utc)
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("job-1", "prop-1", "completed", 5, 5, None, now, now, now),
            ("job-2", "prop-2", "failed", 3, 1, "error msg", now, now, now),
        ]
        mock_pg.return_value = mock_session

        from core.reextractor import get_jobs
        result = get_jobs()

        assert len(result) == 2
        assert result[0]["job_id"] == "job-1"
        assert result[0]["status"] == "completed"
        assert result[1]["job_id"] == "job-2"
        assert result[1]["status"] == "failed"
        assert result[1]["error_message"] == "error msg"

    @patch("core.reextractor._pg_session")
    def test_handles_none_timestamps(self, mock_pg):
        """タイムスタンプがNoneの場合は空文字列を返す。"""
        mock_session = MagicMock()
        mock_session.execute.return_value.fetchall.return_value = [
            ("job-1", "prop-1", "pending", 0, 0, None, None, None, None),
        ]
        mock_pg.return_value = mock_session

        from core.reextractor import get_jobs
        result = get_jobs()

        assert result[0]["started_at"] == ""
        assert result[0]["completed_at"] == ""
        assert result[0]["created_at"] == ""
