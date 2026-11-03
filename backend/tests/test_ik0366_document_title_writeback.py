"""IK-0366: 構造化が取り出した題名を、仮の題名のままの documents.title に書き戻す。

- 仮の題名 = 空 / ファイル名の語幹（``2605.31198v1``）/ ファイル名 / source_path（material_id）。
- 人が編集した題名は上書きしない。冪等。失敗しても解析 run を止めない（fail-soft）。
DB も LLM も使わない（fake セッション）。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.document_pipeline import persistence

TITLE = "Neutron Star Equation of State via Physics Informed Neural Network"
DOC_ID = "7954db7d-528d-488a-8fa2-6e6697ba70e4"


def _structure_artifacts(title=TITLE):
    return {"document_structure": {"metadata": {"title": title, "authors": []}, "sections": []}}


class _Result:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class FakeDocSession:
    """documents 1 行を持つ fake。SELECT / UPDATE と commit / rollback / close を記録する。"""

    def __init__(self, row: dict | None, *, fail_on: str | None = None):
        self.row = dict(row) if row is not None else None
        self.fail_on = fail_on
        self.updates: list[dict] = []
        self.statements: list[str] = []
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        params = params or {}
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("boom")
        if "INSERT INTO document_analysis_runs" in sql:
            return _Result(row=("run-new",))
        if "UPDATE document_analysis_runs" in sql or "document_analysis_artifacts" in sql:
            return _Result(rowcount=1)
        if sql.strip().startswith("SELECT title, filename, source_path"):
            if self.row is None:
                return _Result(row=None)
            return _Result(row=(self.row["title"], self.row.get("filename"), self.row.get("source_path")))
        if "UPDATE documents" in sql:
            if self.row is None or self.row["title"] != params.get("current_title"):
                return _Result(rowcount=0)
            self.row["title"] = params["title"]
            self.updates.append(dict(params))
            return _Result(rowcount=1)
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed += 1


def _run_sync(session, artifacts=None):
    with patch.object(persistence, "_pg_session", return_value=session):
        return persistence.sync_document_title_from_structure(
            DOC_ID, _structure_artifacts() if artifacts is None else artifacts
        )


# --- 純関数 -----------------------------------------------------------------


class TestExtractedTitle:
    def test_reads_metadata_title(self):
        assert persistence.extracted_document_title(_structure_artifacts()) == TITLE

    def test_strips_whitespace(self):
        assert persistence.extracted_document_title(_structure_artifacts(f"  {TITLE} ")) == TITLE

    @pytest.mark.parametrize(
        "bad",
        [None, "", "   ", "Draft version June 2, 2026\nTypeset using LATEX", "x" * 300, 42],
    )
    def test_rejects_unusable_titles(self, bad):
        assert persistence.extracted_document_title(_structure_artifacts(bad)) == ""

    def test_missing_structure_or_metadata(self):
        assert persistence.extracted_document_title({}) == ""
        assert persistence.extracted_document_title({"document_structure": {}}) == ""
        assert persistence.extracted_document_title(None) == ""


class TestPlaceholder:
    @pytest.mark.parametrize(
        "current,filename,source_path",
        [
            ("", "2605.31198v1.pdf", "adda2531-87a"),
            (None, "2605.31198v1.pdf", "adda2531-87a"),
            ("2605.31198v1", "2605.31198v1.pdf", "adda2531-87a"),
            ("2605.31198v1", "2605.31198v1.tar.gz", "adda2531-87a"),
            ("2605.31198v1.pdf", "2605.31198v1.pdf", "adda2531-87a"),
            ("adda2531-87a", None, "adda2531-87a"),
            ("document", None, None),
        ],
    )
    def test_placeholders(self, current, filename, source_path):
        assert persistence.document_title_is_placeholder(
            current, filename=filename, source_path=source_path
        )

    def test_human_title_is_not_placeholder(self):
        assert not persistence.document_title_is_placeholder(
            "中性子星の状態方程式（ゼミ用）", filename="2605.31198v1.pdf", source_path="adda2531-87a"
        )


# --- 書き戻し ---------------------------------------------------------------


def test_writes_back_when_title_is_filename_stem():
    session = FakeDocSession(
        {"title": "2605.31198v1", "filename": "2605.31198v1.pdf", "source_path": "adda2531-87a"}
    )
    assert _run_sync(session) is True
    assert session.row["title"] == TITLE
    assert session.committed == 1
    assert session.closed == 1


def test_does_not_overwrite_human_edited_title():
    session = FakeDocSession(
        {"title": "教員が付けた題名", "filename": "2605.31198v1.pdf", "source_path": "adda2531-87a"}
    )
    assert _run_sync(session) is False
    assert session.row["title"] == "教員が付けた題名"
    assert session.updates == []


def test_idempotent_second_call_is_noop():
    session = FakeDocSession(
        {"title": "2605.31198v1", "filename": "2605.31198v1.pdf", "source_path": "adda2531-87a"}
    )
    assert _run_sync(session) is True
    assert _run_sync(session) is False
    assert len(session.updates) == 1


def test_no_extracted_title_issues_no_sql():
    session = FakeDocSession({"title": "2605.26810v1", "filename": "2605.26810v1.pdf"})
    assert _run_sync(session, _structure_artifacts(None)) is False
    assert session.statements == []


def test_missing_document_row():
    session = FakeDocSession(None)
    assert _run_sync(session) is False


def test_failure_is_fail_soft():
    session = FakeDocSession(
        {"title": "2605.31198v1", "filename": "2605.31198v1.pdf"}, fail_on="UPDATE documents"
    )
    assert _run_sync(session) is False
    assert session.rolled_back == 1
    assert session.closed == 1


# --- upsert_analysis_run からの配線 -----------------------------------------


def test_upsert_analysis_run_writes_title_for_document_structure_stage():
    session = FakeDocSession(
        {"title": "2605.31198v1", "filename": "2605.31198v1.pdf", "source_path": "adda2531-87a"}
    )
    with patch.object(persistence, "_pg_session", return_value=session):
        run_id = persistence.upsert_analysis_run(
            run_id="run-1",
            document_id=DOC_ID,
            material_id="adda2531-87a",
            cartridge_id=None,
            status="running",
            current_stage="document_structure",
            stage_outputs={persistence.ARTIFACTS_KEY: _structure_artifacts()},
        )
    assert run_id == "run-1"
    assert session.row["title"] == TITLE


def test_upsert_analysis_run_other_stage_does_not_touch_documents():
    session = FakeDocSession({"title": "2605.31198v1", "filename": "2605.31198v1.pdf"})
    with patch.object(persistence, "_pg_session", return_value=session):
        persistence.upsert_analysis_run(
            run_id="run-1",
            document_id=DOC_ID,
            material_id="m",
            cartridge_id=None,
            status="running",
            current_stage="paper_skeleton",
            stage_outputs={persistence.ARTIFACTS_KEY: {"paper_skeleton": {"logical_blocks": []}}},
        )
    assert not any("documents" in sql and "document_analysis" not in sql for sql in session.statements)
    assert session.row["title"] == "2605.31198v1"


def test_upsert_analysis_run_survives_title_sync_failure():
    session = FakeDocSession(
        {"title": "2605.31198v1", "filename": "2605.31198v1.pdf"}, fail_on="SELECT title, filename"
    )
    with patch.object(persistence, "_pg_session", return_value=session):
        run_id = persistence.upsert_analysis_run(
            run_id="run-1",
            document_id=DOC_ID,
            material_id="m",
            cartridge_id=None,
            status="running",
            current_stage="document_structure",
            stage_outputs={persistence.ARTIFACTS_KEY: _structure_artifacts()},
        )
    assert run_id == "run-1"
    assert session.row["title"] == "2605.31198v1"
