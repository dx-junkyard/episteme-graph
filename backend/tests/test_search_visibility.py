"""Phase 0（discuss モード設計書 §6.1）— 全域ベクトル検索の可視性フィルタ強制テスト。

背景: `search_chunks_with_metadata`（api/services.py）はコース非依存の全域ベクトル検索で、
document の Public/Group/Private を一切考慮していなかった（他人の Private 文書のチャンクが
誰の検索結果にも出うる潜在バグ）。discuss モードとは独立した既存バグの単独修正として、
Phase 0 で以下を強制する:

- `search_chunks_with_metadata` に `allowed_document_ids` を必須キーワード引数として追加
  （呼び忘れを TypeError で防ぐ。`core/help_kb/manual.py::search_manual(..., audience)` と
  同じ規律）。`None` はテスト・本番未接続コード専用の「無フィルタ」。
- 「本人が閲覧可能な document id 集合」ヘルパー `list_visible_document_ids` を新設
  （チャンク単位ループの N+1 判定は横断基盤の既存ルールで禁止のため、1 SQL で計算する）。

外部 DB・LLM への実接続は行わない（session / embed_text はすべてモック）。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from api import services
from tests.guardrail_helpers import assert_module_tree_forbids

_BACKEND = Path(__file__).resolve().parents[1]
_ROUTES_DIR = _BACKEND / "api" / "routes"


class _Rows:
    """session.execute(...).fetchall() の戻り値スタブ。"""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _CapturingSession:
    """execute() に渡された SQL 文字列と params を記録するフェイクセッション（rows は固定）。"""

    def __init__(self, rows=()):
        self._rows = rows
        self.executed_sql: str | None = None
        self.executed_params: dict | None = None

    def execute(self, sql, params=None):
        self.executed_sql = str(sql)
        self.executed_params = params or {}
        return _Rows(self._rows)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# 1. allowed_document_ids は必須キーワード引数（呼び忘れは TypeError）
# ---------------------------------------------------------------------------


class TestSearchChunksSignatureIsKeywordOnlyRequired:
    def test_missing_allowed_document_ids_raises_type_error(self):
        with pytest.raises(TypeError):
            services.search_chunks_with_metadata("質問")

    def test_positional_allowed_document_ids_is_rejected(self):
        """キーワード専用であることの確認（位置引数では渡せない）。"""
        with pytest.raises(TypeError):
            services.search_chunks_with_metadata("質問", 8, set())

    def test_parameter_is_keyword_only_without_default(self):
        sig = inspect.signature(services.search_chunks_with_metadata)
        param = sig.parameters["allowed_document_ids"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


# ---------------------------------------------------------------------------
# 2. 空集合 → SQL 発行なしで空リスト（fail-closed）
# ---------------------------------------------------------------------------


class TestEmptyAllowedDocumentIdsShortCircuits:
    def test_empty_set_returns_empty_without_embedding_call(self, monkeypatch):
        def _boom(*_a, **_k):
            raise AssertionError("embed_text should not be called when allowed_document_ids is empty")

        monkeypatch.setattr(services, "embed_text", _boom)
        assert services.search_chunks_with_metadata("質問", allowed_document_ids=set()) == []

    def test_empty_list_returns_empty_without_embedding_call(self, monkeypatch):
        def _boom(*_a, **_k):
            raise AssertionError("embed_text should not be called when allowed_document_ids is empty")

        monkeypatch.setattr(services, "embed_text", _boom)
        assert services.search_chunks_with_metadata("質問", allowed_document_ids=[]) == []


# ---------------------------------------------------------------------------
# 3. 非空集合 → SQL に document フィルタ句が入り、material_id は SELECT に残る
# ---------------------------------------------------------------------------


class TestNonEmptyAllowedDocumentIdsFiltersSql:
    def test_sql_contains_document_filter_and_keeps_material_id(self, monkeypatch):
        session = _CapturingSession(rows=[])
        monkeypatch.setattr(services, "embed_text", lambda _q: [0.0] * 8)
        monkeypatch.setattr(services, "get_embedding_dim", lambda: 8)
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        result = services.search_chunks_with_metadata(
            "質問", top_k=5, allowed_document_ids={"doc-1", "doc-2"},
        )

        assert result == []
        assert session.executed_sql is not None
        # component_context.py:131 の ANY(:doc_ids) 先例と同型だが、chunks.document_id は
        # UUID 列（theory_components.document_id は TEXT）なので明示 CAST が要る。
        assert "c.document_id = ANY(CAST(:doc_ids AS uuid[]))" in session.executed_sql
        assert "c.material_id" in session.executed_sql
        assert set(session.executed_params["doc_ids"]) == {"doc-1", "doc-2"}

    def test_none_means_no_filter_test_only(self, monkeypatch):
        """None は無フィルタ（テスト・本番未接続コード専用。docstring にも明記）。"""
        session = _CapturingSession(rows=[])
        monkeypatch.setattr(services, "embed_text", lambda _q: [0.0] * 8)
        monkeypatch.setattr(services, "get_embedding_dim", lambda: 8)
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        services.search_chunks_with_metadata("質問", allowed_document_ids=None)

        assert "document_id = ANY" not in session.executed_sql
        assert "c.material_id" in session.executed_sql
        assert "doc_ids" not in session.executed_params


# ---------------------------------------------------------------------------
# 4. 静的ガードレール: routes/*.py に allowed_document_ids=None が出現しない（呼び忘れ検出）
# ---------------------------------------------------------------------------


class TestNoRouteExplicitlyDisablesTheFilter:
    def test_routes_never_pass_none_explicitly(self):
        assert_module_tree_forbids(_ROUTES_DIR, ["allowed_document_ids=None"])

    def test_learning_and_lecture_wire_list_visible_document_ids(self):
        """本番の呼び出し元2箇所が list_visible_document_ids の結果を渡していること。"""
        learning_src = (_ROUTES_DIR / "learning.py").read_text(encoding="utf-8")
        lecture_src = (_ROUTES_DIR / "lecture.py").read_text(encoding="utf-8")
        for src in (learning_src, lecture_src):
            assert "list_visible_document_ids(current_user[\"id\"])" in src
            assert "allowed_document_ids=allowed_document_ids" in src


# ---------------------------------------------------------------------------
# 5. list_visible_document_ids は例外時に空集合（fail-closed）
# ---------------------------------------------------------------------------


class TestListVisibleDocumentIds:
    def test_exception_returns_empty_set(self, monkeypatch):
        def _boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(services, "_pg_session", _boom)
        assert services.list_visible_document_ids("user-1") == set()

    def test_returns_ids_from_rows(self, monkeypatch):
        session = _CapturingSession(rows=[("doc-a",), ("doc-b",), (None,)])
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        result = services.list_visible_document_ids("user-1")

        assert result == {"doc-a", "doc-b"}  # None 行は落ちる

    def test_sql_covers_all_five_visibility_sources(self, monkeypatch):
        """(a)所有者 (b)public (c)group (d)object_group_permissions (e)コース経由 の
        全経路が1 SQL に含まれていることを構造的に確認する。
        """
        session = _CapturingSession(rows=[])
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        services.list_visible_document_ids("user-1")

        sql = session.executed_sql
        assert sql is not None
        assert "d.uploaded_by = CAST(:uid AS uuid)" in sql  # (a)
        assert "d.visibility = 'public'" in sql  # (b)
        assert "d.visibility = 'group'" in sql and "group_members" in sql  # (c)
        assert "object_group_permissions" in sql  # (d)
        assert "learning_states" in sql and "jsonb_array_elements" in sql  # (e)
        assert session.executed_params == {"uid": "user-1"}
