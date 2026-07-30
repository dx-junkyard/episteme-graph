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


# ---------------------------------------------------------------------------
# 8. document スコープの開幕素材（migration 062）: role の露出と鮮度（§7.1）
#    正本: docs/features/discuss_opening_authoring_design.md §6 / §7.1
# ---------------------------------------------------------------------------


def _document_row(**overrides) -> dict:
    row = {
        "id": "e-doc-1",
        "document_id": "d1",
        "element_type": "document",
        "element_id": "d1",
        "kind": "contextual",
        "role": "discussion_seed",
        "body": "この論文の近似は、どの条件までなら受け入れられるだろうか。",
        "evidence": {
            "evidence_quote": "we linearize the response",
            "reason": "著者が線形化を選んでいる",
            "confidence": 0.72,
            "source_fingerprint": "fp-old",
            "opening_version": "v1",
        },
        "status": "candidate",
        "created_by": "pipeline",
        "reviewed_by": None,
        "reviewed_at": None,
        "created_at": "2026-07-30T00:00:00+00:00",
    }
    row.update(overrides)
    return row


class TestRoleExposedInResponse:
    def test_public_row_source_includes_role(self):
        assert '"role": row.get("role")' in _ROUTE_SRC

    @_skip_no_fastapi
    def test_role_returned_for_document_scope_and_none_for_element_scope(self):
        from routes.element_explanations import _public_row

        assert _public_row(_document_row())["role"] == "discussion_seed"
        # 既存4要素型の行は role=NULL のまま（後方互換）。
        legacy = _public_row(
            {
                "id": "e1",
                "document_id": "d1",
                "element_type": "figure",
                "element_id": "fig-1",
                "kind": "contextual",
                "body": "text",
                "evidence": {"reason": "r"},
                "status": "candidate",
                "created_by": "pipeline",
                "reviewed_by": None,
                "reviewed_at": None,
                "created_at": "2026-07-19T00:00:00+00:00",
            }
        )
        assert legacy["role"] is None

    @_skip_no_fastapi
    def test_document_row_still_hides_raw_confidence(self):
        """開幕素材でも confidence 生値は出さない（E6 / OA6）。"""
        import json

        from routes.element_explanations import _public_row

        dumped = json.dumps(_public_row(_document_row()), ensure_ascii=False)
        assert "0.72" not in dumped
        assert "confidence_label" in dumped


@_skip_no_fastapi
class TestFreshnessOfDocumentScopeRows:
    """§7.1: 生成時の evidence.source_fingerprint と現在の解析結果の指紋を突合する。

    自動で非承認に落とさないため、``approved`` の stale 行にも印だけを付ける
    （レビューキューで見えるようにする）。判定できないときは stale キーを付けない。
    """

    def _rows(self, monkeypatch, rows, artifacts=None, raise_on_read=False):
        import routes.element_explanations as mod

        calls = []

        def fake_artifacts(document_id):
            calls.append(document_id)
            if raise_on_read:
                raise RuntimeError("no run")
            return artifacts if artifacts is not None else {}

        monkeypatch.setattr(mod, "document_run_artifacts", fake_artifacts)
        return mod._public_rows_with_freshness("d1", rows), calls

    def _fingerprint(self, artifacts):
        from core.discuss.authoring import compute_source_fingerprint

        return compute_source_fingerprint(artifacts)

    def test_matching_fingerprint_is_not_stale(self, monkeypatch):
        artifacts = {"thesis_reconstruction": {"central_question": "Q", "central_thesis": {"text": "T"}}}
        row = _document_row()
        row["evidence"]["source_fingerprint"] = self._fingerprint(artifacts)
        out, calls = self._rows(monkeypatch, [row], artifacts=artifacts)
        assert out[0]["stale"] is False
        assert "stale_notice" not in out[0]
        assert calls == ["d1"]

    def test_changed_analysis_marks_row_stale_with_factual_notice(self, monkeypatch):
        artifacts = {"thesis_reconstruction": {"central_question": "Q2", "central_thesis": {"text": "T2"}}}
        out, _calls = self._rows(monkeypatch, [_document_row()], artifacts=artifacts)
        assert out[0]["stale"] is True
        assert out[0]["stale_notice"] == "元の解析結果が変わっています"

    def test_approved_stale_row_stays_approved(self, monkeypatch):
        """承認は自動で外さない（status は approved のまま stale だけが立つ）。"""
        artifacts = {"thesis_reconstruction": {"central_question": "Q2"}}
        row = _document_row(status="approved", reviewed_by="u-teacher")
        out, _calls = self._rows(monkeypatch, [row], artifacts=artifacts)
        assert out[0]["status"] == "approved"
        assert out[0]["stale"] is True

    def test_artifact_read_failure_skips_staleness_fail_open(self, monkeypatch):
        out, calls = self._rows(monkeypatch, [_document_row()], raise_on_read=True)
        assert "stale" not in out[0]
        assert "stale_notice" not in out[0]
        assert calls == ["d1"]

    def test_row_without_stored_fingerprint_is_not_judged(self, monkeypatch):
        row = _document_row()
        row["evidence"].pop("source_fingerprint")
        out, calls = self._rows(monkeypatch, [row])
        assert "stale" not in out[0]
        # 突合対象が無いので artifact を読みに行かない（無駄な I/O を作らない）。
        assert calls == []

    def test_element_scope_rows_are_never_marked(self, monkeypatch):
        figure_row = _document_row(
            id="e2", element_type="figure", element_id="fig-1", role=None
        )
        out, _calls = self._rows(monkeypatch, [figure_row], artifacts={})
        assert "stale" not in out[0]

    def test_fingerprint_computed_once_per_document(self, monkeypatch):
        rows = [_document_row(id="a"), _document_row(id="b"), _document_row(id="c")]
        out, calls = self._rows(monkeypatch, rows, artifacts={"thesis_reconstruction": {}})
        assert len(out) == 3
        assert calls == ["d1"], "行ごとに artifact を再読している"


class TestListRouteWiring:
    def test_list_route_applies_freshness_and_accepts_role_filter(self):
        fn_src = extract_function_source(_ROUTE_SRC, "list_document_element_explanations")
        assert "_public_rows_with_freshness(canonical_document_id, rows)" in fn_src
        assert "role=role" in fn_src
        # 権限ゲートは従来どおり（鮮度の追加で弱めていない）。
        assert "_ensure_document_viewable(" in fn_src

    def test_stale_notice_is_a_factual_sentence(self):
        assert 'STALE_NOTICE = "元の解析結果が変わっています"' in _ROUTE_SRC
        for word in ("！", "今すぐ", "急いで", "至急"):
            assert word not in _ROUTE_SRC

    def test_staleness_never_transitions_status(self):
        """鮮度は表示だけ（自動で非承認に落とさない, §7.1）。"""
        for name in (
            "_public_rows_with_freshness",
            "_current_source_fingerprint",
            "_is_freshness_tracked",
        ):
            fn_src = extract_function_source(_ROUTE_SRC, name)
            assert "store.dismiss" not in fn_src
            assert "UPDATE" not in fn_src
            assert "session" not in fn_src
