"""レビュー確定の修正1（セキュリティ）— チャンク直読み経路の可視性ゲートテスト。

背景1: `GET /api/learning/courses/{course_id}/source-chunk/{chunk_id}`
（``routes/learning.py::get_source_chunk_route``）は chunk_id のみで本文を返しており、
本人が閲覧できない document（Private 文書等）のチャンクでも認証済みユーザーなら誰でも
取得できてしまっていた（可視性ゲートの欠落）。

背景2（2026-07-31 レビュー / DM2）: 同じ欠落が **EXPLAIN_GRAPH_ELEMENT 経路**にも
残っていた。``services.get_graph_element_context`` の主チャンククエリが chunk_id のみで
引くため、受講中の学習者が任意の chunk_id を送るだけで他教員の Private 論文の本文
（プロンプト注入で言い換えて返る）・``target_formula_latex``（逐語前置される）・その
document の承認済み ``element_explanations`` を取得でき、``record_student_stumble_event``
の記帳先も他教員の document になっていた。

対象:
  - ``backend/api/services.py::get_chunk_passage``
    （``allowed_document_ids`` を必須キーワード引数化。``search_chunks_with_metadata``
    と同じ意味論: None=無フィルタ（テスト・未接続コード専用）/ 空集合=SQL 非発行で
    即座に None（fail-closed）/ 集合=SQL 内 ``c.document_id = ANY(...)`` で強制）
  - ``backend/api/services.py::get_graph_element_context``（同じ流儀・空集合は ``{}``）
  - ``backend/api/routes/learning.py::get_source_chunk_route``
    （P0 オブジェクトスコープ是正: 全域可視集合ではなく **URL の course の sources**
    に限定する。``get_accessible_course_data`` → ``list_course_source_document_ids``
    → ``get_chunk_passage(..., allowed_document_ids=...)`` の順。詳細な権限マトリクスは
    ``test_object_scope_authorization.py`` を参照）
  - ``backend/api/routes/learning.py::_generate_graph_element_explanation``
    （``get_chunk_claim_refs`` と同じ複合集合 = コース sources ∪ 本人可視 document を渡す）

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

    def test_learning_wires_course_source_scope_for_source_chunk(self):
        """P0: source-chunk は course sources スコープで fail-closed に絞る。

        全域可視集合（``list_visible_document_ids``）に戻すと、URL の course に
        紐づかない別 course / public 文書のチャンクまで読めてしまう。
        """
        learning_src = (_ROUTES_DIR / "learning.py").read_text(encoding="utf-8")
        block = learning_src.split("def get_source_chunk_route(")[1][:2000]
        assert 'get_accessible_course_data(current_user["id"], course_id)' in block
        assert "allowed_document_ids = list_course_source_document_ids(course_data)" in block
        assert "get_chunk_passage(chunk_id, allowed_document_ids=allowed_document_ids)" in block
        # 全域可視集合を混ぜない（積集合も和集合も取らない）。
        assert "list_visible_document_ids(current_user" not in block


# ---------------------------------------------------------------------------
# 5. ルート: 可視集合外 document のチャンクは 404
# ---------------------------------------------------------------------------


class TestGetSourceChunkRoute:
    def test_404_when_passage_not_found(self, monkeypatch):
        from fastapi import HTTPException
        from api.routes import learning as learning_module

        monkeypatch.setattr(
            learning_module, "get_accessible_course_data", lambda uid, cid: {"sources": []},
        )
        monkeypatch.setattr(
            learning_module, "list_course_source_document_ids", lambda cd: {"doc-1"},
        )
        monkeypatch.setattr(
            learning_module, "get_chunk_passage",
            lambda chunk_id, allowed_document_ids=None: None,
        )

        with pytest.raises(HTTPException) as exc_info:
            learning_module.get_source_chunk_route(
                "course-1", "chunk-1", current_user={"id": "user-1"},
            )
        assert exc_info.value.status_code == 404

    def test_passes_course_source_document_ids_to_service(self, monkeypatch):
        from api.routes import learning as learning_module

        captured: dict = {}

        monkeypatch.setattr(
            learning_module, "get_accessible_course_data", lambda uid, cid: {"sources": []},
        )
        monkeypatch.setattr(
            learning_module, "list_course_source_document_ids", lambda cd: {"doc-a", "doc-b"},
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

    def test_out_of_course_chunk_yields_404_via_empty_allowed_set(self, monkeypatch):
        """コースの sources が0件（＝チャンクの document が course スコープ外）なら、
        get_chunk_passage は空集合の fail-closed 短絡で None を返し、ルートは 404 にする。
        （learning.py の `from services import get_chunk_passage` は top-level `services`
        モジュールとして再ロードされるため、ここでは `services._pg_session` の差し替えではなく
        allowed_document_ids=set() の短絡経路で実関数をそのまま実行し検証する。）
        """
        from fastapi import HTTPException
        from api.routes import learning as learning_module

        monkeypatch.setattr(
            learning_module, "get_accessible_course_data", lambda uid, cid: {"sources": []},
        )
        monkeypatch.setattr(
            learning_module, "list_course_source_document_ids", lambda cd: set(),
        )

        with pytest.raises(HTTPException) as exc_info:
            learning_module.get_source_chunk_route(
                "course-1", "chunk-other-users", current_user={"id": "user-1"},
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 6. EXPLAIN_GRAPH_ELEMENT 経路（DM2）: get_graph_element_context の可視性ゲート
# ---------------------------------------------------------------------------


class _MultiExecuteSession:
    """execute() が呼ばれた順に用意された結果を返すフェイクセッション。

    get_graph_element_context は主チャンク（fetchone）→ 関連チャンク（fetchall）の
    最大2クエリを発行するため、_CapturingSession（1クエリ固定）を拡張する。
    """

    def __init__(self, *, main_row=None, related_rows=()):
        self._main_row = main_row
        self._related_rows = list(related_rows)
        self.executed: list[tuple[str, dict]] = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params or {}))
        session = self
        index = len(self.executed)

        class _Result:
            def fetchone(self_inner):
                return session._main_row if index == 1 else None

            def fetchall(self_inner):
                return session._related_rows if index > 1 else []

        return _Result()

    def close(self):
        self.closed = True


# get_graph_element_context の主クエリ SELECT 列順:
#   c.id, c.text, c.display_text, c.formulas, c.material_id, source_title,
#   d.knowledge_graph, d.uploaded_by, c.source_metadata
_GRAPH_MAIN_ROW = (
    "chunk-1", "raw text", "display text", [], "mat-1", "論文タイトル",
    {"concepts": [{"id": "cid", "name": "概念", "description": "説明文"}]},
    "instructor-1", {},
)


class TestGetGraphElementContextSignatureIsKeywordOnlyRequired:
    def test_missing_allowed_document_ids_raises_type_error(self):
        with pytest.raises(TypeError):
            services.get_graph_element_context({}, "chunk-1", "cid")

    def test_positional_allowed_document_ids_is_rejected(self):
        with pytest.raises(TypeError):
            services.get_graph_element_context({}, "chunk-1", "cid", "concept", "概念", {"doc-1"})

    def test_parameter_is_keyword_only_without_default(self):
        sig = inspect.signature(services.get_graph_element_context)
        param = sig.parameters["allowed_document_ids"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


class TestGetGraphElementContextFailsClosed:
    def test_empty_set_returns_empty_dict_without_sql(self, monkeypatch):
        def _boom():
            raise AssertionError("_pg_session must not be called for an empty allowed set")

        monkeypatch.setattr(services, "_pg_session", _boom)
        assert services.get_graph_element_context(
            {}, "chunk-1", "cid", "concept", "概念", allowed_document_ids=set(),
        ) == {}

    def test_empty_list_returns_empty_dict_without_sql(self, monkeypatch):
        def _boom():
            raise AssertionError("_pg_session must not be called for an empty allowed set")

        monkeypatch.setattr(services, "_pg_session", _boom)
        assert services.get_graph_element_context(
            {}, "chunk-1", "cid", allowed_document_ids=[],
        ) == {}

    def test_chunk_outside_allowed_set_returns_empty_dict(self, monkeypatch):
        """SQL は発行されるが可視集合内に該当チャンクが無ければ行が返らず {}。
        （= 他教員の Private 論文のチャンク本文・数式・knowledge_graph が漏れない）"""
        session = _MultiExecuteSession(main_row=None)
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        result = services.get_graph_element_context(
            {"sources": [{"material_id": "mat-1"}]},
            "chunk-of-another-teacher", "cid", "concept", "概念",
            allowed_document_ids={"doc-visible-only"},
        )
        assert result == {}
        # 関連チャンククエリまで進まない（主クエリのみ）。
        assert len(session.executed) == 1


class TestGetGraphElementContextFiltersSql:
    def test_sql_contains_document_filter_and_params(self, monkeypatch):
        session = _MultiExecuteSession(main_row=_GRAPH_MAIN_ROW)
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        result = services.get_graph_element_context(
            {}, "chunk-1", "cid", "concept", "概念",
            allowed_document_ids={"doc-1", "doc-2"},
        )

        assert result["chunk_id"] == "chunk-1"
        main_sql, main_params = session.executed[0]
        assert "c.document_id = ANY(CAST(:doc_ids AS uuid[]))" in main_sql
        assert set(main_params["doc_ids"]) == {"doc-1", "doc-2"}
        assert session.closed is True

    def test_none_means_no_filter_test_only(self, monkeypatch):
        session = _MultiExecuteSession(main_row=_GRAPH_MAIN_ROW)
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        result = services.get_graph_element_context(
            {}, "chunk-1", "cid", "concept", "概念", allowed_document_ids=None,
        )

        main_sql, main_params = session.executed[0]
        assert result["chunk_id"] == "chunk-1"
        assert "document_id = ANY" not in main_sql
        assert "doc_ids" not in main_params

    def test_course_source_chunk_still_resolves_normally(self, monkeypatch):
        """コース sources のチャンク（可視集合内）は従来どおり文脈が組み立てられる
        — 可視性ゲートの追加で既存の学習体験を壊していないことの回帰確認。"""
        session = _MultiExecuteSession(
            main_row=_GRAPH_MAIN_ROW,
            related_rows=[("chunk-2", "関連本文", "論文タイトル")],
        )
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        result = services.get_graph_element_context(
            {"sources": [{"material_id": "mat-1"}]},
            "chunk-1", "cid", "concept", "概念",
            allowed_document_ids={"doc-1"},
        )

        assert result["material_id"] == "mat-1"
        assert result["graph_description"] == "説明文"
        assert result["instructor_id"] == "instructor-1"
        assert [c["id"] for c in result["related_chunks"]] == ["chunk-2"]
        # secondary（関連チャンク）は従来どおり course_source_material_ids でスコープ済み。
        related_sql, related_params = session.executed[1]
        assert "c.material_id IN (" in related_sql
        assert related_params["mid_0"] == "mat-1"


class TestGraphElementRouteWiring:
    def test_route_passes_composite_allowed_set(self):
        """静的検査: _generate_graph_element_explanation が
        「コース sources ∪ 本人可視 document」の複合集合を必須引数として渡すこと。"""
        learning_src = (_ROUTES_DIR / "learning.py").read_text(encoding="utf-8")
        block = learning_src.split("def _generate_graph_element_explanation(")[1][:2600]
        assert "list_course_source_document_ids(course_data)" in block
        assert "list_visible_document_ids(user_id)" in block
        assert "allowed_document_ids=allowed_document_ids" in block

    def test_invisible_chunk_yields_404_and_records_no_stumble_event(self, monkeypatch):
        """本人可視 document が0件・コース sources も0件なら、実 get_graph_element_context の
        空集合短絡で {} が返り 404。student_stumble_events への記帳（他教員 document への
        書き込み）も起きない。"""
        from fastapi import HTTPException
        from api.routes import learning as learning_module
        from schemas import LearningChatRequest

        monkeypatch.setattr(learning_module, "list_course_source_document_ids", lambda cd: set())
        monkeypatch.setattr(learning_module, "list_visible_document_ids", lambda uid: set())

        def _unexpected_stumble(**kwargs):
            raise AssertionError("stumble event must not be recorded for an invisible chunk")

        monkeypatch.setattr(learning_module, "record_student_stumble_event", _unexpected_stumble)

        body = LearningChatRequest(
            message="", chunk_id="chunk-of-another-teacher",
            element_id="cid", element_type="concept",
        )
        with pytest.raises(HTTPException) as exc_info:
            learning_module._generate_graph_element_explanation(
                user_id="student-1", course_id="c1", topic_id="t1",
                course_title="コース", topic_title="トピック",
                course_data={}, body=body,
            )
        assert exc_info.value.status_code == 404

    def test_route_unions_course_sources_and_visible_documents(self, monkeypatch):
        from api.routes import learning as learning_module
        from schemas import LearningChatRequest

        captured: dict = {}

        monkeypatch.setattr(
            learning_module, "list_course_source_document_ids", lambda cd: {"doc-course"},
        )
        monkeypatch.setattr(
            learning_module, "list_visible_document_ids", lambda uid: {"doc-own"},
        )
        monkeypatch.setattr(learning_module, "record_student_stumble_event", lambda **kw: None)
        monkeypatch.setattr(learning_module, "persist_chat_history", lambda *a, **kw: None)

        def _fake_context(course_data, chunk_id, element_id, element_type=None,
                          element_label=None, *, allowed_document_ids):
            captured["allowed_document_ids"] = allowed_document_ids
            return {
                "chunk_id": chunk_id,
                "chunk_text": "本文",
                "formulas": [],
                "material_id": "mat-1",
                "source_title": "論文タイトル",
                "instructor_id": None,
                "element_id": element_id,
                "element_type": "concept",
                "element_label": "概念",
                "target_formula": None,
                "target_citation": None,
                "target_reference": None,
                "graph_description": "概念の説明。",
                "related_chunks": [],
            }

        monkeypatch.setattr(learning_module, "get_graph_element_context", _fake_context)

        body = LearningChatRequest(
            message="", chunk_id="chunk-1", element_id="cid", element_type="concept",
        )
        resp = learning_module._generate_graph_element_explanation(
            user_id="student-1", course_id="c1", topic_id="t1",
            course_title="コース", topic_title="トピック",
            course_data={"sources": [{"material_id": "mat-1"}]}, body=body,
        )

        assert captured["allowed_document_ids"] == {"doc-course", "doc-own"}
        assert "概念の説明。" in resp.answer
