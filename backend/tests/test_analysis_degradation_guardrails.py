"""解析の縮退の投影（IK-0715）のガードレール。

1. ``core/analysis_degradation.py`` は FastAPI / sqlalchemy / LLM / A層を直接 import しない。
   推移的には FastAPI / LLM SDK / A層が載らない（表示名の正本 ``core.llm_policy`` を遅延
   import するため sqlalchemy は推移的に載り得る — DB セッションは作らない）。
2. 判定は 1 箇所（route-all-surfaces-through-one-point）: 教材行・グラフレビュー・G層・
   完了時フックがすべて ``summarize_run_degradation`` を呼び、縮退の rule_id を
   自前で走査しない。
3. 完了時フックは ``_stage_completed`` に置かれ、新しいステージを増やさない。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.guardrail_helpers import assert_source_does_not_import, extract_function_source

BACKEND = Path(__file__).resolve().parents[1]
_CORE = BACKEND / "core" / "analysis_degradation.py"
_CORE_SRC = _CORE.read_text(encoding="utf-8")
_ADMIN_SRC = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
_TC_SRC = (BACKEND / "api" / "routes" / "theory_components.py").read_text(encoding="utf-8")
_NS_SRC = (BACKEND / "core" / "admin_assistant" / "next_steps.py").read_text(encoding="utf-8")
_ORCH_SRC = (BACKEND / "core" / "document_pipeline" / "orchestrator.py").read_text(encoding="utf-8")


class TestPurity:
    def test_no_framework_imports(self):
        assert_source_does_not_import(
            _CORE_SRC,
            ["fastapi", "sqlalchemy", "core.postgres", "openai", "episteme_graph", "core.llm "],
            context="core/analysis_degradation.py",
        )
        assert "get_session" not in _CORE_SRC

    def test_transitively_pure(self):
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "import core.analysis_degradation as d\n"
            "d.stage_label('dsl_linking')\n"
            "bad = [m for m in sys.modules if m.split('.')[0] in ('fastapi','openai','anthropic','episteme_graph')]\n"
            "print(','.join(sorted(bad)))\n"
        ) % str(BACKEND)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == ""


class TestSinglePoint:
    def test_materials_list_uses_projection(self):
        fn = extract_function_source(_ADMIN_SRC, "_material_analysis_degradation")
        assert "summarize_run_degradation" in fn and "public_view" in fn
        list_fn = extract_function_source(_ADMIN_SRC, "list_materials")
        assert "_material_analysis_degradation(stage_outputs)" in list_fn
        assert "analysis_degradation=analysis_degradation" in list_fn

    def test_graph_review_uses_projection(self):
        fn = extract_function_source(_TC_SRC, "_graph_analysis_degradation")
        assert "summarize_run_degradation" in fn and "resolve_artifact_runs" in fn
        assert "_graph_analysis_degradation(document_id)" in extract_function_source(_TC_SRC, "get_component_graph")

    def test_guidance_rule_uses_projection(self):
        fn = extract_function_source(_NS_SRC, "_eval_material_analysis_llm_failed")
        assert "summarize_run_degradation" in fn
        assert "'analysis_degradation'" in fn

    def test_completion_hook_writes_snapshot(self):
        hook = extract_function_source(_ORCH_SRC, "_completion_degradation_outputs")
        assert "summarize_run_degradation" in hook and "DEGRADATION_KEY" in hook
        assert "**degradation_outputs" in extract_function_source(_ORCH_SRC, "_stage_completed")
        # 単一ステージ実行の完了も同じ投影で残す
        assert "_completion_degradation_outputs(previous_artifacts, target_llm_failures)" in _ORCH_SRC

    def test_no_second_decision_table(self):
        """縮退の rule_id を判定に使うのは core の 1 ファイルだけ（面ごとに書かない）。"""
        surfaces = {
            "admin.py": _ADMIN_SRC,
            "theory_components.py": _TC_SRC,
            "next_steps.py": _NS_SRC,
        }
        js = BACKEND.parent / "frontend" / "public" / "js"
        for name in ("admin.js", "admin-graph-review.js"):
            surfaces[name] = (js / name).read_text(encoding="utf-8")
        for rule_id in ("thesis_reconstruction_failed", "dsl_linking_deterministic_fallback",
                        "component_assembly_deterministic_fallback"):
            for name, src in surfaces.items():
                assert rule_id not in src, f"{name} が縮退の判定を自前で持っている: {rule_id}"

    def test_not_a_new_pipeline_stage(self):
        src_dir = str(BACKEND.parent / "src")
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        from core.document_pipeline import orchestrator as orch

        assert "analysis_degradation" not in orch.PIPELINE_STAGES
