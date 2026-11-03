"""題名の起動時バックフィル（IK-0419）。

題名の書き戻し（IK-0366 ``sync_document_title_from_structure``）より前に解析された教材は
題名がファイル名・arXiv ID のまま残る。採用 run の ``document_structure`` から題名が採れる
教材だけを、起動時に1回・冪等に直す。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for p in (str(ROOT / "src"), str(BACKEND), str(BACKEND / "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.document_pipeline import persistence  # noqa: E402


class _Result:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)


class _Session:
    def __init__(self, documents, artifacts):
        self.documents = {d[0]: list(d) for d in documents}
        self.artifacts = artifacts  # {doc_id: payload}
        self.calls: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = dict(params or {})
        self.calls.append((sql, params))
        if sql.startswith("SELECT id::text, title, filename, source_path FROM documents"):
            return _Result([tuple(v) for v in self.documents.values()])
        if "a.stage = 'document_structure'" in sql:
            ids = [v for k, v in params.items() if k.startswith("doc_")]
            return _Result(
                [(did, json.dumps(self.artifacts[did])) for did in ids if did in self.artifacts]
            )
        if sql.startswith("UPDATE documents SET title"):
            row = self.documents.get(params["id"])
            if row is not None and row[1] == params["current_title"]:
                row[1] = params["title"]
                return _Result(rowcount=1)
            return _Result(rowcount=0)
        return _Result()


def _structure(title):
    return {"metadata": {"title": title}}


def test_placeholder_titles_are_replaced_once():
    session = _Session(
        documents=[
            ("d1", "2606.02318v1", "2606.02318v1.pdf", "mat-1"),
            ("d2", "人が付けた題名", "2605.31198v1.pdf", "mat-2"),
            ("d3", "2605.26810v1", "2605.26810v1.pdf", "mat-3"),
        ],
        artifacts={
            "d1": _structure("Delay-time distribution of BBHs in GWTC-4"),
            "d2": _structure("Should not overwrite"),
            "d3": _structure("Broken\ntitle"),
        },
    )
    assert persistence.backfill_document_titles_from_structure(session) == 1
    assert session.documents["d1"][1] == "Delay-time distribution of BBHs in GWTC-4"
    assert session.documents["d2"][1] == "人が付けた題名"  # 人の題名は触らない
    assert session.documents["d3"][1] == "2605.26810v1"  # 採れない題名は推測しない
    # 2回目は対象が減り、何も書かない（冪等）
    assert persistence.backfill_document_titles_from_structure(session) == 0


def test_only_document_structure_stage_of_adopted_run_is_read():
    session = _Session(
        documents=[("d1", "2606.02318v1", "2606.02318v1.pdf", "mat-1")],
        artifacts={"d1": _structure("T")},
    )
    persistence.backfill_document_titles_from_structure(session)
    sql = next(c[0] for c in session.calls if "a.stage = 'document_structure'" in c[0])
    assert "active_analysis_run_id" in sql  # 採用 run の選び方（adopted）
    assert "stage_outputs" not in sql


def test_no_placeholder_documents_issue_no_artifact_query():
    session = _Session(documents=[("d1", "Real title", "x.pdf", "mat-1")], artifacts={})
    assert persistence.backfill_document_titles_from_structure(session) == 0
    assert not any("document_structure" in c[0] for c in session.calls)


def test_lifespan_runs_backfill_under_its_own_advisory_lock():
    src = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
    assert "backfill_document_titles_from_structure" in src
    assert "DOCUMENT_TITLE_BACKFILL_LOCK_KEY" in src
    assert persistence.DOCUMENT_TITLE_BACKFILL_LOCK_KEY != __import__(
        "core.knowledge_objects.backfill", fromlist=["BACKFILL_LOCK_KEY"]
    ).BACKFILL_LOCK_KEY
