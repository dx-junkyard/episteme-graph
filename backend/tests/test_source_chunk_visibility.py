"""レビュー確定の修正1（セキュリティ）— source-chunk API の可視性ゲートテスト。

背景: `GET /api/learning/courses/{course_id}/source-chunk/{chunk_id}`
（``routes/learning.py::get_source_chunk_route``）は chunk_id のみで本文を返しており、
本人が閲覧できない document（Private 文書等）のチャンクでも認証済みユーザーなら誰でも
取得できてしまっていた（可視性ゲートの欠落）。

対象:
  - ``backend/api/services.py::get_chunk_passage``
    （``allowed_document_ids`` を必須キーワード引数化。``search_chunks_with_metadata``
    と同じ意味論: None=無フィルタ（テスト・未接続コード専用）/ 空集合=SQL 非発行で
    即座に None（fail-closed）/ 集合=SQL 内 ``c.document_id = ANY(...)`` で強制）
  - ``backend/api/routes/learning.py::get_source_chunk_route``
    （``list_visible_document_ids(current_user["id"])`` を渡す）

test_search_visibility.py の既存フェイクセッションパターンに倣う。外部 DB・LLM への
実接続は行わない。
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

# learning.py 内部は `from dependencies import ...` 等の裸 import に依存するため、
# backend/api を sys.path に載せる（test_learner_claim_ledger_refs.py と同型）。
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from api import services
from tests.guardrail_helpers import assert_module_tree_forbids

_BACKEND = Path(__file__).resolve().parents[1]
_ROUTES_DIR = _BACKEND / "api" / "routes"


class _Row:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _CapturingSession:
    """execute() に渡された SQL 文字列と params を記録するフェイクセッション（row は固定）。"""

    def __init__(self, row=None):
        self._row = row
        self.executed_sql: str | None = None
        self.executed_params: dict | None = None
        self.closed = False

    def execute(self, sql, params=None):
        self.executed_sql = str(sql)
        self.executed_params = params or {}
        return _Row(self._row)

    def close(self):
        self.closed = True


_SAMPLE_ROW = (
    "raw text", "display text", [], "chapter 1", "section 1", "論文タイトル", "paper.pdf",
)


# ---------------------------------------------------------------------------
# 1. allowed_document_ids は必須キーワード引数
# ---------------------------------------------------------------------------


class TestGetChunkPassageSignatureIsKeywordOnlyRequired:
    def test_missing_allowed_document_ids_raises_type_error(self):
        with pytest.raises(TypeError):
            services.get_chunk_passage("chunk-1")

    def test_positional_allowed_document_ids_is_rejected(self):
        with pytest.raises(TypeError):
            services.get_chunk_passage("chunk-1", {"doc-1"})

    def test_parameter_is_keyword_only_without_default(self):
        sig = inspect.signature(services.get_chunk_passage)
        param = sig.parameters["allowed_document_ids"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# 2. 空集合 → SQL 発行なしで None（fail-closed）
# ---------------------------------------------------------------------------


class TestEmptyAllowedDocumentIdsShortCircuits:
    def test_empty_set_returns_none_without_sql(self, monkeypatch):
        def _boom():
            raise AssertionError("_pg_session should not be called when allowed_document_ids is empty")

        monkeypatch.setattr(services, "_pg_session", _boom)
        assert services.get_chunk_passage("chunk-1", allowed_document_ids=set()) is None

    def test_empty_list_returns_none_without_sql(self, monkeypatch):
        def _boom():
            raise AssertionError("_pg_session should not be called when allowed_document_ids is empty")

        monkeypatch.setattr(services, "_pg_session", _boom)
        assert services.get_chunk_passage("chunk-1", allowed_document_ids=[]) is None


# ---------------------------------------------------------------------------
# 3. 非空集合 → SQL に document フィルタ句が入る／可視集合外は None
# ---------------------------------------------------------------------------


class TestNonEmptyAllowedDocumentIdsFiltersSql:
    def test_sql_contains_document_filter(self, monkeypatch):
        session = _CapturingSession(row=_SAMPLE_ROW)
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        result = services.get_chunk_passage(
            "chunk-1", allowed_document_ids={"doc-1", "doc-2"},
        )

        assert result is not None
        assert session.executed_sql is not None
        assert "c.document_id = ANY(CAST(:doc_ids AS uuid[]))" in session.executed_sql
        assert set(session.executed_params["doc_ids"]) == {"doc-1", "doc-2"}
        assert session.closed is True

    def test_none_means_no_filter_test_only(self, monkeypatch):
        """None は無フィルタ（テスト・本番未接続コード専用。docstring にも明記）。"""
        session = _CapturingSession(row=_SAMPLE_ROW)
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        result = services.get_chunk_passage("chunk-1", allowed_document_ids=None)

        assert result is not None
        assert "document_id = ANY" not in session.executed_sql
        assert "doc_ids" not in session.executed_params

    def test_row_not_found_returns_none(self, monkeypatch):
        """SQL 自体は発行されるが、可視集合内に該当チャンクが無ければ行が返らず None。"""
        session = _CapturingSession(row=None)
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        result = services.get_chunk_passage(
            "chunk-1", allowed_document_ids={"doc-visible-only"},
        )
        assert result is None


# ---------------------------------------------------------------------------
# 4. 静的ガードレール: routes/*.py に allowed_document_ids=None が出現しない
# ---------------------------------------------------------------------------


class TestNoRouteExplicitlyDisablesTheFilter:
    def test_routes_never_pass_none_explicitly(self):
        assert_module_tree_forbids(_ROUTES_DIR, ["allowed_document_ids=None"])

    def test_learning_wires_list_visible_document_ids_for_source_chunk(self):
        learning_src = (_ROUTES_DIR / "learning.py").read_text(encoding="utf-8")
        block = learning_src.split("def get_source_chunk_route(")[1][:900]
        assert 'list_visible_document_ids(current_user["id"])' in block
        assert "get_chunk_passage(chunk_id, allowed_document_ids=allowed_document_ids)" in block


# ---------------------------------------------------------------------------
# 5. ルート: 可視集合外 document のチャンクは 404
# ---------------------------------------------------------------------------


class TestGetSourceChunkRoute:
    def test_404_when_passage_not_found(self, monkeypatch):
        from fastapi import HTTPException
        from api.routes import learning as learning_module

        monkeypatch.setattr(learning_module, "list_visible_document_ids", lambda uid: {"doc-1"})
        monkeypatch.setattr(
            learning_module, "get_chunk_passage",
            lambda chunk_id, allowed_document_ids=None: None,
        )

        with pytest.raises(HTTPException) as exc_info:
            learning_module.get_source_chunk_route(
                "course-1", "chunk-1", current_user={"id": "user-1"},
            )
        assert exc_info.value.status_code == 404

    def test_passes_visible_document_ids_to_service(self, monkeypatch):
        from api.routes import learning as learning_module

        captured: dict = {}

        monkeypatch.setattr(
            learning_module, "list_visible_document_ids", lambda uid: {"doc-a", "doc-b"},
        )

        def _fake_get_chunk_passage(chunk_id, allowed_document_ids=None):
            captured["chunk_id"] = chunk_id
            captured["allowed_document_ids"] = allowed_document_ids
            return {"chunk_id": chunk_id, "text": "本文", "formulas": [],
                    "source_title": "t", "source_file": "f", "section": ""}

        monkeypatch.setattr(learning_module, "get_chunk_passage", _fake_get_chunk_passage)

        result = learning_module.get_source_chunk_route(
            "course-1", "chunk-1", current_user={"id": "user-1"},
        )
        assert result["chunk_id"] == "chunk-1"
        assert captured["allowed_document_ids"] == {"doc-a", "doc-b"}

    def test_invisible_document_chunk_yields_404_via_empty_allowed_set(self, monkeypatch):
        """本人が閲覧可能な document が0件（＝チャンクの document が可視集合外）なら、
        get_chunk_passage は空集合の fail-closed 短絡で None を返し、ルートは 404 にする。
        （learning.py の `from services import get_chunk_passage` は top-level `services`
        モジュールとして再ロードされるため、ここでは `services._pg_session` の差し替えではなく
        allowed_document_ids=set() の短絡経路で実関数をそのまま実行し検証する。）
        """
        from fastapi import HTTPException
        from api.routes import learning as learning_module

        monkeypatch.setattr(learning_module, "list_visible_document_ids", lambda uid: set())

        with pytest.raises(HTTPException) as exc_info:
            learning_module.get_source_chunk_route(
                "course-1", "chunk-other-users", current_user={"id": "user-1"},
            )
        assert exc_info.value.status_code == 404
