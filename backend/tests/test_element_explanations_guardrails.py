"""要素説明の二層台帳（``element_explanations``, Phase 2, migration 056）ガードレール。

正本: ``docs/features/hierarchical_context_explanation_design.md`` §2（E1〜E8）・§5.2・§8。
本ファイルはこのエージェントのスコープ（core ストア + 承認 API + migration）に閉じた
最小ガードレールで、Phase 2 全体（ContextualExplanationAgent 等）向けの
``test_contextual_explanation_guardrails.py`` は別スコープ（後続エージェント）。

1. ``core/element_explanations.py`` が FastAPI を import しない
2. ``routes/element_explanations.py`` に DELETE ルートが無い（静的・動的の両方で検証）
3. 承認 API の応答が confidence 生値を返さない（段階ラベルのみ）
4. 監査 entity_type がカタログ定数（``AUDIT_ENTITY_ELEMENT_EXPLANATION``）経由である
5. core / API のどちらにも ``DELETE FROM element_explanations`` が無い（P4: 行削除しない）
6. 再解析（``insert_candidates``）が既存の ``approved``/``dismissed`` 行を書き換えない

外部サービス（PostgreSQL 等）への実接続は行わない。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

_CORE_SRC = (BACKEND / "core" / "element_explanations.py").read_text(encoding="utf-8")
_ROUTE_SRC = (BACKEND / "api" / "routes" / "element_explanations.py").read_text(encoding="utf-8")
_SCHEMA_SRC = (BACKEND / "core" / "schema.py").read_text(encoding="utf-8")

_HAS_FASTAPI = True
try:
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)


# ---------------------------------------------------------------------------
# 1. core が FastAPI を import しない
# ---------------------------------------------------------------------------


class TestCoreDoesNotImportFastapi:
    def test_element_explanations_core_forbids_fastapi(self):
        assert_source_does_not_import(
            _CORE_SRC, ["fastapi"], context="core/element_explanations.py",
        )

    def test_element_explanations_core_forbids_services_and_routes(self):
        # 開発ルール2・W1相当: core/ は routes/services に依存しない。
        assert_source_forbids(
            _CORE_SRC,
            ["from routes", "import routes", "from services", "import services"],
            context="core/element_explanations.py",
        )


# ---------------------------------------------------------------------------
# 2. DELETE ルートが無い（P4: 行削除 API は作らない）
# ---------------------------------------------------------------------------


class TestNoDeleteRoute:
    def test_no_delete_decorator_in_source(self):
        assert_source_forbids(
            _ROUTE_SRC, ["@router.delete"], context="routes/element_explanations.py",
        )

    @_skip_no_fastapi
    def test_no_delete_route_registered_dynamically(self):
        import routes.element_explanations as element_explanations_routes

        methods: set[str] = set()
        for route in element_explanations_routes.router.routes:
            methods.update(getattr(route, "methods", set()) or set())
        assert "DELETE" not in methods

    def test_core_and_routes_never_issue_raw_delete_sql(self):
        assert_source_forbids(
            _CORE_SRC, ["DELETE FROM element_explanations"], context="core/element_explanations.py",
        )
        assert_source_forbids(
            _ROUTE_SRC, ["DELETE FROM element_explanations"], context="routes/element_explanations.py",
        )

    def test_status_transitions_not_deletes(self):
        # 却下・再解析・編集はいずれも status 遷移 (superseded/dismissed) のみで、
        # 行そのものを消す経路が core に存在しないことをソースレベルでも確認する。
        assert "STATUS_SUPERSEDED" in _CORE_SRC
        assert "STATUS_DISMISSED" in _CORE_SRC
        assert "def insert_candidates" in _CORE_SRC
        assert "def update_body" in _CORE_SRC


# ---------------------------------------------------------------------------
# 3. confidence 生値を API が返さない（段階ラベルのみ・E6）
# ---------------------------------------------------------------------------


class TestConfidenceLabelOnly:
    def test_public_row_source_pops_raw_confidence(self):
        assert "confidence_label" in _ROUTE_SRC
        assert 'evidence.pop("confidence"' in _ROUTE_SRC

    @_skip_no_fastapi
    def test_public_row_strips_raw_confidence_at_runtime(self):
        from routes.element_explanations import _public_row

        row = {
            "id": "e1",
            "document_id": "d1",
            "element_type": "figure",
            "element_id": "fig-1",
            "kind": "contextual",
            "body": "text",
            "evidence": {"reason": "r", "confidence": 0.913, "evidence_quote": "q"},
            "status": "candidate",
            "created_by": "pipeline",
            "reviewed_by": None,
            "reviewed_at": None,
            "created_at": "2026-07-19T00:00:00+00:00",
        }
        public = _public_row(row)
        assert "confidence" not in public["evidence"]
        assert "confidence_label" in public["evidence"]
        assert isinstance(public["evidence"]["confidence_label"], str)

        import json
        dumped = json.dumps(public, ensure_ascii=False)
        assert "0.913" not in dumped


# ---------------------------------------------------------------------------
# 4. 監査 entity_type がカタログ定数経由である
# ---------------------------------------------------------------------------


class TestAuditUsesCatalogConstant:
    def test_schema_defines_and_registers_constant(self):
        assert 'AUDIT_ENTITY_ELEMENT_EXPLANATION = "element_explanation"' in _SCHEMA_SRC
        # カタログ tuple への登録も必須(ガードレール対象)。
        catalog_start = _SCHEMA_SRC.index("AUDIT_ENTITY_TYPES = (")
        catalog_body = _SCHEMA_SRC[catalog_start:catalog_start + 2000]
        assert "AUDIT_ENTITY_ELEMENT_EXPLANATION," in catalog_body or (
            "AUDIT_ENTITY_ELEMENT_EXPLANATION" in catalog_body
        )

    def test_routes_import_constant_rather_than_literal(self):
        assert "from core.schema import AUDIT_ENTITY_ELEMENT_EXPLANATION" in _ROUTE_SRC
        assert '"element_explanation"' not in _ROUTE_SRC
        assert "'element_explanation'" not in _ROUTE_SRC

    def test_every_record_review_event_call_uses_the_constant(self):
        calls = _ROUTE_SRC.split("record_review_event(")[1:]
        assert len(calls) >= 3, "expected approve/dismiss/edit to all audit"
        for call in calls:
            first_line = call.strip().splitlines()[0]
            assert "AUDIT_ENTITY_ELEMENT_EXPLANATION" in first_line


# ---------------------------------------------------------------------------
# 5. 再解析が approved/dismissed を書き換えない（migration 053 と同じ原則）
# ---------------------------------------------------------------------------


class TestReanalysisPreservesReviewedRows:
    def test_insert_candidates_supersede_guard_is_status_scoped(self):
        # insert_candidates の supersede UPDATE が status='candidate' の行のみを
        # 対象にしていること（approved/dismissed 行の WHERE 句に status フィルタが
        # 無いと誤って巻き込む恐れがあるため、静的にガードの存在を確認する）。
        assert "AND status = :candidate" in _CORE_SRC


# ---------------------------------------------------------------------------
# 6. migration 056 が冪等であること（新規ファイルの最低限の自己検証）
# ---------------------------------------------------------------------------


class TestMigrationIdempotent:
    def test_migration_056_uses_if_not_exists(self):
        sql = read_migration_sql(BACKEND, 56)
        assert "CREATE TABLE IF NOT EXISTS" in sql
        assert "CREATE INDEX IF NOT EXISTS" in sql
        assert "DROP TABLE" not in sql
        assert "DELETE FROM" not in sql


# ---------------------------------------------------------------------------
# 7. 一括承認/却下（bulk-review）: 権限ゲート・上限・監査 bulk フラグ
# ---------------------------------------------------------------------------


class TestBulkReviewEndpoint:
    def test_bulk_review_max_items_constant_exists(self):
        assert "BULK_REVIEW_MAX_ITEMS" in _ROUTE_SRC

    def test_bulk_review_route_goes_through_editable_gate(self):
        # 一括操作は承認・却下のみ1件版と同じ強さの権限ゲート(_ensure_document_editable)を
        # 通ること — レビュー負荷軽減のために権限チェックを弱めていないかの静的検査。
        fn_src = extract_function_source(_ROUTE_SRC, "bulk_review_element_explanations")
        assert "_ensure_document_editable(" in fn_src

    def test_bulk_review_audits_with_bulk_flag_and_catalog_constant(self):
        fn_src = extract_function_source(_ROUTE_SRC, "bulk_review_element_explanations")
        assert '"bulk": True' in fn_src
        assert "AUDIT_ENTITY_ELEMENT_EXPLANATION" in fn_src
        assert "record_review_event(" in fn_src

    @_skip_no_fastapi
    def test_bulk_review_route_registered_as_post_only(self):
        import routes.element_explanations as element_explanations_routes

        matched = [
            route
            for route in element_explanations_routes.router.routes
            if getattr(route, "path", "").endswith("/element-explanations/bulk-review")
        ]
        assert len(matched) == 1
        methods = matched[0].methods or set()
        assert "POST" in methods
        assert "DELETE" not in methods
