"""IK-0362: AI 提供元の障害で欠けた呼び出しを run の記録と G層に届ける。

- U層の失敗行（``llm_usage_events.success=false``）を run 完了時に集計し、
  ``stage_outputs["llm_failures"]`` に事実ブロックとして残す（失敗が無ければ書かない）。
- 事実文（``note`` / G層 reason）に件数・提供元の生メッセージ・URL を書かない。
- 利用上限・残高の制限（``RateLimitError``）は言い分ける。
- 要約の失敗は run の状態を変えない（fail-soft）。
- G層 ``material.analysis_llm_failed`` が採用 run のブロックを読み、再実行へ道案内する。

実 DB には接続しない（fake session / monkeypatch のみ）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.admin_assistant import capabilities as caps  # noqa: E402
from core.admin_assistant import next_steps as ns  # noqa: E402
from core.document_pipeline import orchestrator as orch  # noqa: E402
from core.llm_usage import recorder  # noqa: E402
from core.llm_usage import run_failures as rf  # noqa: E402
from core.llm_usage.schema import UsageEvent  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    extract_function_source,
)

_RUN_ID = "11111111-2222-3333-4444-555555555555"
_ORCH_SRC = (BACKEND / "core" / "document_pipeline" / "orchestrator.py").read_text(encoding="utf-8")
_RF_SRC = (BACKEND / "core" / "llm_usage" / "run_failures.py").read_text(encoding="utf-8")
_NS_SRC = (BACKEND / "core" / "admin_assistant" / "next_steps.py").read_text(encoding="utf-8")


def _row(feature, error_type, failed):
    return {"feature": feature, "error_type": error_type, "failed": failed}


# ---------------------------------------------------------------------------
# 1. 純関数: build_llm_failures_block
# ---------------------------------------------------------------------------


class TestBuildBlock:
    def test_none_when_no_failures(self):
        assert rf.build_llm_failures_block([]) is None
        assert rf.build_llm_failures_block([_row("pipeline:x", "RateLimitError", 0)]) is None

    def test_rate_limit_block(self):
        block = rf.build_llm_failures_block([
            _row("pipeline:equation_semantics", "RateLimitError", 265),
            _row("pipeline:component_assembly", "RateLimitError", 2),
        ])
        assert block["status"] == "provider_failures_detected"
        assert block["features"] == ["pipeline:component_assembly", "pipeline:equation_semantics"]
        assert block["stages"] == ["component_assembly", "equation_semantics"]
        assert block["error_types"] == ["RateLimitError"]
        assert block["rate_limited"] is True
        assert "利用上限または残高の制限" in block["note"]
        # 件数は run 内部の記録にだけ置く
        assert block["failed_calls"] == 267
        assert {"feature": "pipeline:equation_semantics", "error_type": "RateLimitError",
                "failed": 265} in block["by_feature"]

    def test_note_has_no_numbers_or_urls(self):
        for error_type in ("RateLimitError", "AuthenticationError", "APIConnectionError"):
            block = rf.build_llm_failures_block([_row("pipeline:dsl_linking", error_type, 12)])
            assert not any(ch.isdigit() for ch in block["note"])
            assert "http" not in block["note"]

    def test_other_and_auth_wording(self):
        other = rf.build_llm_failures_block([_row("pipeline:dsl_linking", "APITimeoutError", 1)])
        assert other["rate_limited"] is False
        assert other["note"] == rf.NOTE_OTHER
        auth = rf.build_llm_failures_block([_row("pipeline:dsl_linking", "AuthenticationError", 1)])
        assert auth["note"] == rf.NOTE_AUTH

    def test_rate_limit_wins_when_mixed(self):
        block = rf.build_llm_failures_block([
            _row("pipeline:a", "APITimeoutError", 1),
            _row("pipeline:b", "RateLimitError", 1),
        ])
        assert block["note"] == rf.NOTE_RATE_LIMIT

    def test_tokens_are_sanitized(self):
        """クラス名・feature 名以外（空白・URL の断片）を保存しない。"""
        block = rf.build_llm_failures_block([
            _row("pipeline:x https://platform.example/billing", "RateLimitError: see https://x/y", 1),
        ])
        assert block["error_types"] == ["RateLimitError"]  # クラス名だけ
        for token in block["features"] + block["error_types"]:
            assert " " not in token
            assert "//" not in token

    def test_classify(self):
        assert rf.classify_error_type("RateLimitError") == rf.CAUSE_RATE_LIMIT
        assert rf.classify_error_type("PermissionDeniedError") == rf.CAUSE_AUTH
        assert rf.classify_error_type("InternalServerError") == rf.CAUSE_OTHER
        assert rf.classify_error_type("") == rf.CAUSE_OTHER


# ---------------------------------------------------------------------------
# 2. summarize_run_llm_failures（DB 行 + まだ flush されていないバッファ）
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows=None, *, fail=False):
        self.rows = rows or []
        self.fail = fail
        self.queries: list[tuple[str, dict]] = []
        self.rolled_back = False

    def execute(self, stmt, params=None):
        self.queries.append((str(stmt), params or {}))
        if self.fail:
            raise RuntimeError("db down")
        return _FakeResult(self.rows)

    def rollback(self):
        self.rolled_back = True


@pytest.fixture
def clean_recorder():
    recorder.reset_for_tests()
    yield
    recorder.reset_for_tests()


def _event(run_id, *, success, error_type=None, feature="pipeline:discuss_opening"):
    return UsageEvent(
        provider="openai", model="m", operation="chat", feature=feature,
        usage_source="estimated_heuristic", success=success, error_type=error_type,
        run_id=run_id,
    )


class TestSummarize:
    def test_queries_this_run_failures_only(self, clean_recorder):
        session = _FakeSession([_row("pipeline:landscape_placement", "RateLimitError", 6)])
        block = rf.summarize_run_llm_failures(session, _RUN_ID, flush=False)
        assert block["features"] == ["pipeline:landscape_placement"]
        sql, params = session.queries[0]
        assert "llm_usage_events" in sql
        assert "success = FALSE" in sql
        assert "run_id = CAST(:run_id AS uuid)" in sql
        assert "GROUP BY feature" in sql
        assert params == {"run_id": _RUN_ID}

    def test_none_when_db_has_no_failures(self, clean_recorder):
        assert rf.summarize_run_llm_failures(_FakeSession([]), _RUN_ID, flush=False) is None

    def test_includes_unflushed_buffer_events_of_this_run(self, clean_recorder):
        with recorder._lock:
            recorder._buffer.append(_event(_RUN_ID, success=False, error_type="RateLimitError"))
            recorder._buffer.append(_event(_RUN_ID, success=True))
            recorder._buffer.append(_event("other-run", success=False, error_type="APIError"))
        block = rf.summarize_run_llm_failures(_FakeSession([]), _RUN_ID, flush=False)
        assert block is not None
        assert block["error_types"] == ["RateLimitError"]
        assert block["failed_calls"] == 1

    def test_merges_db_and_buffer(self, clean_recorder):
        with recorder._lock:
            recorder._buffer.append(_event(_RUN_ID, success=False, error_type="RateLimitError"))
        session = _FakeSession([_row("pipeline:discuss_opening", "RateLimitError", 5)])
        block = rf.summarize_run_llm_failures(session, _RUN_ID, flush=False)
        assert block["failed_calls"] == 6
        assert len(block["by_feature"]) == 1

    def test_db_failure_is_soft(self, clean_recorder):
        session = _FakeSession(fail=True)
        assert rf.summarize_run_llm_failures(session, _RUN_ID, flush=False) is None
        assert session.rolled_back

    def test_empty_run_id(self, clean_recorder):
        session = _FakeSession([_row("pipeline:x", "RateLimitError", 1)])
        assert rf.summarize_run_llm_failures(session, None) is None
        assert rf.summarize_run_llm_failures(session, "") is None
        assert session.queries == []

    def test_flush_is_attempted_first(self, clean_recorder, monkeypatch):
        calls = []
        monkeypatch.setattr(recorder, "flush_now", lambda: calls.append("flush") or 0)
        rf.summarize_run_llm_failures(_FakeSession([]), _RUN_ID)
        assert calls == ["flush"]

    def test_flush_exception_does_not_leak(self, clean_recorder, monkeypatch):
        def boom():
            raise RuntimeError("x")
        monkeypatch.setattr(recorder, "flush_now", boom)
        session = _FakeSession([_row("pipeline:x", "RateLimitError", 1)])
        assert rf.summarize_run_llm_failures(session, _RUN_ID) is not None

    def test_module_is_pure_of_fastapi_and_llm(self):
        assert_source_does_not_import(_RF_SRC, ["fastapi", "openai"], context="run_failures.py")
        assert "core.llm " not in _RF_SRC and "from core import llm" not in _RF_SRC
        assert "DELETE FROM" not in _RF_SRC.upper()


# ---------------------------------------------------------------------------
# 3. orchestrator の完了記録
# ---------------------------------------------------------------------------


class TestOrchestratorCompletion:
    def test_outputs_empty_when_no_failures(self, monkeypatch):
        monkeypatch.setattr(orch, "_llm_failures_snapshot", lambda _rid: None)
        assert orch._completion_llm_failures_outputs(_RUN_ID) == {}

    def test_outputs_empty_when_snapshot_raises(self, monkeypatch):
        def boom(_rid):
            raise RuntimeError("x")
        monkeypatch.setattr(orch, "_llm_failures_snapshot", boom)
        assert orch._completion_llm_failures_outputs(_RUN_ID) == {}

    def test_outputs_block_when_failures(self, monkeypatch):
        block = {"status": "provider_failures_detected", "note": rf.NOTE_OTHER}
        monkeypatch.setattr(orch, "_llm_failures_snapshot", lambda _rid: block)
        assert orch._completion_llm_failures_outputs(_RUN_ID) == {"llm_failures": block}

    def test_snapshot_is_soft_when_session_unavailable(self, monkeypatch):
        import core.postgres as pg

        def boom():
            raise RuntimeError("no db")
        monkeypatch.setattr(pg, "get_session", boom)
        assert orch._llm_failures_snapshot(_RUN_ID) is None

    def _run_stage_completed(self, monkeypatch, block):
        captured: list[dict] = []
        monkeypatch.setattr(orch, "_llm_failures_snapshot", lambda _rid: block)
        monkeypatch.setattr(orch, "_reference_health_snapshot", lambda _d: {"status": "ok"})
        monkeypatch.setattr(orch, "upsert_analysis_run", lambda **kw: captured.append(kw))
        monkeypatch.setattr(orch, "set_active_analysis_run", lambda **kw: None)
        monkeypatch.setattr(orch, "get_active_analysis_run_id", lambda **kw: None)
        import core.doubt.ledger_builder as lb
        import core.doubt.load_calculator as lc
        import core.doubt.scope_candidates.worker as sw
        monkeypatch.setattr(lb, "backfill_document_ledger", lambda **kw: None)
        monkeypatch.setattr(lc, "recompute_load_scores", lambda **kw: None)
        monkeypatch.setattr(sw, "maybe_schedule_scope_candidates", lambda **kw: None)
        reported = []
        ctx = SimpleNamespace(
            run_id=_RUN_ID, document_id="doc-1", material_id="mat_1", cartridge_id=None,
            course_id=None,
            result=SimpleNamespace(chunk_count=1, claim_count=2, component_count=3,
                                   dsl_node_count=0, dsl_edge_count=0, final_stage=""),
            report_done=lambda *a, **kw: reported.append((a, kw)),
        )
        orch._stage_completed(ctx)
        return captured, reported, ctx

    def test_stage_completed_writes_block_and_keeps_status_completed(self, monkeypatch):
        block = {"status": "provider_failures_detected", "note": rf.NOTE_RATE_LIMIT,
                 "rate_limited": True}
        captured, reported, ctx = self._run_stage_completed(monkeypatch, block)
        completed = captured[0]
        assert completed["status"] == "completed"
        assert completed["stage_outputs"]["llm_failures"] == block
        assert completed["stage_outputs"]["reference_health"] == {"status": "ok"}
        assert ctx.result.final_stage == "completed"
        assert reported[-1][1] == {"run_status": "completed"}

    def test_stage_completed_writes_nothing_without_failures(self, monkeypatch):
        captured, _reported, _ctx = self._run_stage_completed(monkeypatch, None)
        assert "llm_failures" not in captured[0]["stage_outputs"]
        assert captured[0]["status"] == "completed"

    def test_single_stage_completion_also_records(self):
        body = _ORCH_SRC[_ORCH_SRC.index("def finish_target_stage"):]
        body = body[: body.index("def all_artifacts")]
        assert "_completion_llm_failures_outputs(run_id)" in body

    def test_completion_does_not_change_status_vocabulary(self):
        src = extract_function_source(_ORCH_SRC, "_stage_completed")
        assert 'status="completed"' in src
        assert "llm_failures_outputs" in src


# ---------------------------------------------------------------------------
# 4. G層 material.analysis_llm_failed
# ---------------------------------------------------------------------------


class _RowsSession:
    def __init__(self, rows):
        self.rows = rows
        self.queries: list[tuple[str, dict]] = []

    def execute(self, stmt, params=None):
        self.queries.append((str(stmt), params or {}))
        return _FakeResult(self.rows)


def _material(block):
    return {
        "id": "d-uuid-1",
        "source_path": "mat_1",
        "title": "重力波の波形モデル",
        "created_at": "2026-09-01T00:00:00+00:00",
        "llm_failures": block,
    }


class TestGuidanceRule:
    def test_registered_as_recommended_rerun_guidance(self):
        rule = ns.RULE_CATALOG[ns.RULE_MATERIAL_ANALYSIS_LLM_FAILED]
        assert rule["severity"] == ns.SEVERITY_RECOMMENDED
        assert rule["capability_id"] == "materials.rerun_pipeline"
        cap = caps.get_capability("materials.rerun_pipeline")
        assert cap is not None and cap.kind == "guidance_only"
        assert ns._RULE_EVALUATORS[ns.RULE_MATERIAL_ANALYSIS_LLM_FAILED] is ns._eval_material_analysis_llm_failed

    def test_reads_adopted_run_llm_failures(self):
        src = extract_function_source(_NS_SRC, "_eval_material_analysis_llm_failed")
        assert "_ADOPTED_RUN_SQL" in src
        assert "'llm_failures'" in src
        assert "jsonb_typeof" in src

    def test_rate_limited_reason(self):
        out = ns._eval_material_analysis_llm_failed(
            _RowsSession([_material({"status": "provider_failures_detected", "rate_limited": True})]),
            "u1",
        )
        assert len(out) == 1
        step, sort_ts = out[0]
        assert step.rule_id == ns.RULE_MATERIAL_ANALYSIS_LLM_FAILED
        assert step.step_key == "material.analysis_llm_failed:d-uuid-1"
        assert step.target == {"material_id": "d-uuid-1"}
        assert sort_ts == "2026-09-01T00:00:00+00:00"
        assert "重力波の波形モデル" in step.reason
        assert "利用上限または残高の制限" in step.reason
        assert "成果の一部が欠けている可能性があります" in step.reason
        assert "再実行で補えます" in step.reason
        anchors = [s["anchor_id"] for s in step.locate_plan["steps"]]
        assert "material_row:mat_1" in anchors

    def test_generic_reason(self):
        step, _ = ns._eval_material_analysis_llm_failed(
            _RowsSession([_material({"status": "provider_failures_detected", "rate_limited": False})]),
            "u1",
        )[0]
        assert "AI 提供元の呼び出しが失敗し" in step.reason
        assert "利用上限" not in step.reason

    def test_reason_has_no_numbers_or_raw_messages(self):
        block = {"status": "provider_failures_detected", "rate_limited": True,
                 "failed_calls": 265, "note": "x", "error_types": ["RateLimitError"]}
        step, _ = ns._eval_material_analysis_llm_failed(_RowsSession([_material(block)]), "u1")[0]
        assert not any(ch.isdigit() for ch in step.reason)
        assert "RateLimitError" not in step.reason
        assert "http" not in step.reason

    def test_no_rows_no_steps(self):
        assert ns._eval_material_analysis_llm_failed(_RowsSession([]), "u1") == []

    def test_non_dict_block_falls_back_to_generic(self):
        step, _ = ns._eval_material_analysis_llm_failed(_RowsSession([_material("broken")]), "u1")[0]
        assert "AI 提供元の呼び出しが失敗し" in step.reason
