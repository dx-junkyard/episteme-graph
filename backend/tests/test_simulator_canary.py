"""カナリアリリース（scope="canary"）のガードレールテスト。

対象:
  1. SQL インジェクション — course_ids / exclude_ids は必ずバインドパラメータで渡し、
     SQL 文字列へ値を埋め込まないこと（TEACHER 権限のリクエストボディ由来）。
  2. 実際に絞ること — カナリアは対象コース由来のドキュメントだけを再抽出し、
     全件（status='completed' の全ドキュメント）へフォールバックしないこと。
  3. 0 件は 0 件 — 対象が無ければ SQL を発行せず、正直に 0 件で完了すること。

DB は一切触らない（session をモックしてパラメータを検査する）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.guardrail_helpers import assert_paths_forbid

# 典型的なインジェクション payload（承認 API のボディに入りうる文字列）
_INJECTION_IDS = [
    "course-1",
    "x' OR '1'='1",
    "'); DROP TABLE documents; --",
    "\\'; DELETE FROM chunks; --",
]

_SIMULATOR_PY = Path(__file__).resolve().parents[1] / "core" / "simulator.py"
_REEXTRACTOR_PY = Path(__file__).resolve().parents[1] / "core" / "reextractor.py"


class _RecordingSession:
    """session.execute の (SQL, params) を記録するフェイクセッション。"""

    def __init__(self, results: dict[str, list] | None = None):
        self.calls: list[tuple[str, dict | None]] = []
        self._results = results or {}
        self.closed = False

    def execute(self, statement, params=None):  # noqa: D102
        sql = str(statement)
        self.calls.append((sql, params))
        rows: list = []
        for marker, value in self._results.items():
            if marker in sql:
                rows = value
                break
        result = MagicMock()
        result.fetchall.return_value = rows
        result.fetchone.return_value = rows[0] if rows else None
        return result

    def commit(self):  # noqa: D102
        pass

    def rollback(self):  # noqa: D102
        pass

    def close(self):  # noqa: D102
        self.closed = True

    # --- 検査ヘルパー -----------------------------------------------------
    def sql_containing(self, marker: str) -> list[tuple[str, dict | None]]:
        return [c for c in self.calls if marker in c[0]]

    @property
    def all_sql(self) -> str:
        return "\n".join(c[0] for c in self.calls)


def _assert_no_literal_injection(session: _RecordingSession) -> None:
    """発行された SQL 文字列にインジェクション payload が現れないこと。"""
    for sql, _params in session.calls:
        for payload in _INJECTION_IDS[1:]:
            assert payload not in sql, f"値が SQL 文字列に埋め込まれている: {sql}"
    assert "DROP TABLE" not in session.all_sql
    assert "DELETE FROM chunks" not in session.all_sql


# ---------------------------------------------------------------------------
# 1. SQL インジェクション（バインドパラメータ化）
# ---------------------------------------------------------------------------


class TestCanaryDocumentIdsBinding:
    """_canary_target_document_ids のバインド検査。"""

    @patch("core.simulator._pg_session")
    def test_course_ids_are_bound_not_interpolated(self, mock_pg):
        from core.simulator import _canary_target_document_ids

        session = _RecordingSession({"FROM documents": [("doc-1",), ("doc-2",)]})
        mock_pg.return_value = session

        doc_ids = _canary_target_document_ids(_INJECTION_IDS)

        assert doc_ids == ["doc-1", "doc-2"]
        _assert_no_literal_injection(session)

        sql, params = session.calls[0]
        assert "= ANY(:course_ids)" in sql
        assert params is not None
        assert params["course_ids"] == _INJECTION_IDS
        assert session.closed is True

    @patch("core.simulator._pg_session")
    def test_empty_course_ids_emits_no_sql(self, mock_pg):
        from core.simulator import _canary_target_document_ids

        assert _canary_target_document_ids([]) == []
        mock_pg.assert_not_called()


class TestDocumentSelectionBinding:
    """Similar / Control 選出の除外IDもバインドすること。"""

    @patch("core.simulator._pg_session")
    def test_similar_documents_bind_exclude_ids(self, mock_pg):
        from core.simulator import _select_similar_documents

        session = _RecordingSession({"FROM documents": []})
        mock_pg.return_value = session

        _select_similar_documents(_INJECTION_IDS)

        _assert_no_literal_injection(session)
        sql, params = session.calls[0]
        assert "<> ALL(:exclude_ids)" in sql
        assert params["exclude_ids"] == _INJECTION_IDS

    @patch("core.simulator._pg_session")
    def test_control_documents_bind_exclude_ids(self, mock_pg):
        from core.simulator import _select_control_documents

        session = _RecordingSession({"FROM documents": []})
        mock_pg.return_value = session

        _select_control_documents(_INJECTION_IDS)

        _assert_no_literal_injection(session)
        sql, params = session.calls[0]
        assert "<> ALL(:exclude_ids)" in sql
        assert params["exclude_ids"] == _INJECTION_IDS

    @patch("core.simulator._pg_session")
    def test_similar_documents_empty_input_returns_empty(self, mock_pg):
        from core.simulator import _select_similar_documents

        assert _select_similar_documents([]) == []
        mock_pg.assert_not_called()


class TestNoFStringSqlInSource:
    """simulator / reextractor に f-string・% 補間の SQL を残さない（構造的禁止）。"""

    def test_sources_have_no_interpolated_sql(self):
        assert_paths_forbid(
            [_SIMULATOR_PY, _REEXTRACTOR_PY],
            ['sa_text(f"', "sa_text(f'", "placeholders = "],
        )


# ---------------------------------------------------------------------------
# 2. カナリアが実際にドキュメントを絞ること
# ---------------------------------------------------------------------------


class TestEnqueueCanary:
    """_enqueue_canary_reextraction は解決済みドキュメントIDをジョブへ渡す。"""

    @patch("threading.Thread")
    @patch("core.simulator._pg_session")
    @patch("core.simulator._canary_target_document_ids")
    def test_total_docs_matches_resolved_documents(self, mock_resolve, mock_pg, mock_thread):
        from core.simulator import _enqueue_canary_reextraction

        mock_resolve.return_value = ["doc-1", "doc-2", "doc-3"]
        session = _RecordingSession()
        mock_pg.return_value = session

        job = _enqueue_canary_reextraction("prop-1", ["course-1"])

        assert job["total_docs"] == 3
        assert job["processed_docs"] == 0
        assert job["status"] == "pending"

        # ジョブ登録は INSERT のみ（件数は解決結果から導出する）
        inserts = session.sql_containing("INSERT INTO reextraction_jobs")
        assert len(inserts) == 1
        assert inserts[0][1]["total_docs"] == 3

        # スレッドには解決済み document_ids がそのまま渡る
        args = mock_thread.call_args.kwargs["args"]
        assert args[0] == job["job_id"]
        assert args[1] == ["course-1"]
        assert args[2] == ["doc-1", "doc-2", "doc-3"]

    @patch("threading.Thread")
    @patch("core.simulator._pg_session")
    @patch("core.simulator._canary_target_document_ids")
    def test_zero_documents_is_reported_honestly(self, mock_resolve, mock_pg, mock_thread):
        from core.simulator import _enqueue_canary_reextraction

        mock_resolve.return_value = []
        session = _RecordingSession()
        mock_pg.return_value = session

        job = _enqueue_canary_reextraction("prop-1", ["course-x"])

        assert job["total_docs"] == 0
        # 0 件でも全件へ化けない（空リストがそのまま渡る）
        assert mock_thread.call_args.kwargs["args"][2] == []


class TestRunCanaryReextraction:
    """_run_canary_reextraction は document_ids を必ず下流へ渡す。"""

    @patch("core.reextractor._run_reextraction_job")
    def test_passes_given_document_ids(self, mock_run):
        from core.simulator import _run_canary_reextraction

        _run_canary_reextraction("job-1", ["course-1"], ["doc-1", "doc-2"])

        mock_run.assert_called_once_with("job-1", document_ids=["doc-1", "doc-2"])

    @patch("core.reextractor._run_reextraction_job")
    @patch("core.simulator._canary_target_document_ids")
    def test_resolves_document_ids_when_omitted(self, mock_resolve, mock_run):
        from core.simulator import _run_canary_reextraction

        mock_resolve.return_value = ["doc-9"]

        _run_canary_reextraction("job-1", ["course-1"])

        mock_resolve.assert_called_once_with(["course-1"])
        mock_run.assert_called_once_with("job-1", document_ids=["doc-9"])

    @patch("core.reextractor._run_reextraction_job")
    @patch("core.simulator._canary_target_document_ids")
    def test_empty_resolution_does_not_fall_back_to_all(self, mock_resolve, mock_run):
        from core.simulator import _run_canary_reextraction

        mock_resolve.return_value = []

        _run_canary_reextraction("job-1", ["course-x"])

        # document_ids=None（=全件）で呼ばれてはならない
        mock_run.assert_called_once_with("job-1", document_ids=[])


# ---------------------------------------------------------------------------
# 3. _run_reextraction_job の絞り込み
# ---------------------------------------------------------------------------


class TestRunReextractionJobScoping:
    """document_ids による絞り込みと 0 件の扱い。"""

    @pytest.fixture(autouse=True)
    def _stub_services(self):
        """``services`` は api/ 配下（テスト単体では import 不可）なのでスタブ化する。

        本テストはドキュメント取得クエリだけを見るため、対象 0 件で処理ループには入らない。
        """
        with patch.dict(sys.modules, {"services": MagicMock()}):
            yield

    @patch("core.reextractor._pg_session")
    def test_filtered_query_binds_doc_ids(self, mock_pg):
        from core.reextractor import _run_reextraction_job

        session = _RecordingSession({"FROM documents": []})
        mock_pg.return_value = session

        _run_reextraction_job("job-1", document_ids=["doc-1", "doc-2"])

        selects = session.sql_containing("FROM documents")
        assert len(selects) == 1
        sql, params = selects[0]
        assert "= ANY(:doc_ids)" in sql
        assert params["doc_ids"] == ["doc-1", "doc-2"]
        _assert_no_literal_injection(session)

    @patch("core.reextractor._pg_session")
    def test_empty_document_ids_emits_no_document_select(self, mock_pg):
        from core.reextractor import _run_reextraction_job

        session = _RecordingSession()
        mock_pg.return_value = session

        _run_reextraction_job("job-1", document_ids=[])

        # 対象 0 件では documents への SELECT を発行しない（全件フォールバック禁止）
        assert session.sql_containing("FROM documents") == []

        finals = session.sql_containing("UPDATE reextraction_jobs")
        assert finals, "ジョブ状態は更新されること"
        completed = [c for c in finals if c[1] and c[1].get("status") == "completed"]
        assert len(completed) == 1
        assert completed[0][1]["processed"] == 0

    @patch("core.reextractor._pg_session")
    def test_none_document_ids_keeps_full_scan(self, mock_pg):
        """document_ids=None（scope="full"）は従来どおり全件クエリ。"""
        from core.reextractor import _run_reextraction_job

        session = _RecordingSession({"FROM documents": []})
        mock_pg.return_value = session

        _run_reextraction_job("job-1")

        selects = session.sql_containing("FROM documents")
        assert len(selects) == 1
        assert ":doc_ids" not in selects[0][0]
