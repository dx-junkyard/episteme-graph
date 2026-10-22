"""解析の縮退（IK-0715）の API 面: 教材一覧の ``MaterialOut.analysis_degradation`` と
グラフレビューの ``ComponentGraphResponse.analysis_degradation``、G層ルールの点灯。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for path in (str(BACKEND), os.path.join(str(BACKEND), "api"), str(ROOT / "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

_DEGRADED_SO = {
    "component_assembly": {"status": "completed", "fallback": True, "fallback_component_count": 5,
                           "fallback_reason": "Agent LLM call exceeded 330.0s"},
}


class TestMaterialRow:
    def test_schema_has_field(self):
        from api.schemas import MaterialOut

        assert "analysis_degradation" in MaterialOut.model_fields

    def test_degraded_run_projects_facts_without_digits(self):
        from api.routes.admin import _material_analysis_degradation

        view = _material_analysis_degradation(_DEGRADED_SO)
        assert view["status"] == "degraded"
        assert set(view) == {"status", "facts"}
        assert view["facts"] and all(not any(c.isdigit() for c in f) for f in view["facts"])
        assert "exceeded" not in "".join(view["facts"])

    def test_snapshot_ok_run(self):
        from api.routes.admin import _material_analysis_degradation

        view = _material_analysis_degradation({"analysis_degradation": {"status": "ok", "facts": []}})
        assert view == {"status": "ok", "facts": []}

    def test_unknown_run_is_unchecked(self):
        from api.routes.admin import _material_analysis_degradation

        assert _material_analysis_degradation(None) == {"status": "unchecked", "facts": []}


class TestGraphResponse:
    def test_schema_has_field(self):
        from api.schemas import ComponentGraphResponse

        assert "analysis_degradation" in ComponentGraphResponse.model_fields

    def test_graph_degradation_reads_adopted_run_artifacts(self):
        from api.routes import theory_components as tc

        artifacts = {"thesis_reconstruction": {"validation_issues": [
            {"rule_id": "thesis_reconstruction_failed", "message": "timeout"}]}}

        class _S:
            def close(self):
                pass

        with patch.object(tc, "_pg_session", return_value=_S()), patch(
            "core.document_pipeline.persistence.resolve_artifact_runs",
            return_value={"d1": {"stage_outputs": {"_artifacts": artifacts}}},
        ):
            view = tc._graph_analysis_degradation("d1")
        assert view["status"] == "degraded"
        assert "中心命題の再構成" in view["facts"][0]

    def test_graph_degradation_fail_soft(self):
        from api.routes import theory_components as tc

        with patch.object(tc, "_pg_session", side_effect=RuntimeError("db down")):
            assert tc._graph_analysis_degradation("d1") == {"status": "unchecked", "facts": []}


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, stmt, params=None):
        return _Result(self.rows)


def _row(**extra):
    base = {"id": "d-1", "source_path": "mat_1", "title": "重力波", "created_at": "2026-10-01T00:00:00+00:00",
            "llm_failures": None, "analysis_degradation": None, "component_assembly": None}
    base.update(extra)
    return base


class TestGuidanceRule:
    def test_fallback_only_run_lights(self):
        from core.admin_assistant import next_steps as ns

        out = ns._eval_material_analysis_llm_failed(
            _Session([_row(component_assembly=_DEGRADED_SO["component_assembly"])]), "u1"
        )
        assert len(out) == 1
        step, _ = out[0]
        assert "決定論の代替" in step.reason
        assert "理論コンポーネントの組み立て" in step.reason
        assert not any(c.isdigit() for c in step.reason)

    def test_snapshot_degraded_lights(self):
        from core.admin_assistant import next_steps as ns

        snap = {"status": "degraded", "facts": ["「概念グラフへの接続」は AI の応答が得られず、決定論の代替で作られています。"]}
        out = ns._eval_material_analysis_llm_failed(_Session([_row(analysis_degradation=snap)]), "u1")
        assert len(out) == 1 and "概念グラフへの接続" in out[0][0].reason

    def test_ok_snapshot_does_not_light(self):
        from core.admin_assistant import next_steps as ns

        out = ns._eval_material_analysis_llm_failed(
            _Session([_row(analysis_degradation={"status": "ok", "facts": []})]), "u1"
        )
        assert out == []

    def test_llm_failures_wording_unchanged(self):
        from core.admin_assistant import next_steps as ns

        out = ns._eval_material_analysis_llm_failed(
            _Session([_row(llm_failures={"rate_limited": True})]), "u1"
        )
        assert "利用上限または残高の制限" in out[0][0].reason


class TestCompletionHook:
    def test_hook_writes_snapshot(self):
        from core.document_pipeline import orchestrator as orch

        out = orch._completion_degradation_outputs(
            {"dsl_linking": {"validation_issues": [{"rule_id": "dsl_linking_failed", "message": "x"}]}}, {}
        )
        assert out["analysis_degradation"]["status"] == "degraded"
        clean = orch._completion_degradation_outputs({"dsl_linking": {"validation_issues": []}}, {})
        assert clean["analysis_degradation"]["status"] == "ok"
