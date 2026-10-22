"""解析の縮退の読み時投影（IK-0715）— ``core/analysis_degradation.py`` の純関数。

固定するのは:

1. agent が縮退時に書く信号（rule_id / review_notes / maturity_source / skipped_reason /
   llm_failures / 完了ペイロード）から ``degraded`` を導くこと。
2. 事実文に数字・例外文（打ち切りの秒数・提供元メッセージ）が入らないこと。
3. artifact を読めた run は信号ゼロなら ``ok``、読めず完了時スナップショットも無ければ
   ``unchecked``（問題なしと偽装しない）。
4. 完了時スナップショットがあればそれを使う（教材一覧が artifact を読み直さない）。
5. 例外を外に出さない。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import analysis_degradation as ad  # noqa: E402

_TIMEOUT = "Agent LLM call exceeded 330.0s"


def _issue(rule_id, message=_TIMEOUT):
    return {"rule_id": rule_id, "severity": "error", "message": message, "field": "x"}


def _no_digits(facts):
    return all(not any(ch.isdigit() for ch in f) for f in facts)


class TestSignals:
    def test_thesis_dsl_component_fallbacks_are_degraded(self):
        artifacts = {
            "thesis_reconstruction": {"validation_issues": [_issue("thesis_reconstruction_failed")]},
            "dsl_linking": {"validation_issues": [_issue("dsl_linking_deterministic_fallback", "LLM DSL linking failed; emitted 7 fallback node(s)")]},
            "component_assembly": {
                "diagnostics": {"fallback_reason": _TIMEOUT},
                "components": [{"maturity_source": "deterministic_fallback"}] * 3,
            },
        }
        out = ad.summarize_run_degradation({}, artifacts=artifacts)
        assert out["status"] == ad.STATUS_DEGRADED
        assert set(out["stages"]) == {"thesis_reconstruction", "dsl_linking", "component_assembly"}
        assert _no_digits(out["facts"])
        joined = "".join(out["facts"])
        assert "中心命題の再構成" in joined
        assert "決定論の代替" in joined
        assert "330" not in joined and "exceeded" not in joined
        # 件数は details にだけ
        assert out["details"]["stages"]["component_assembly"]["count"] == 3

    def test_paper_skeleton_review_note(self):
        out = ad.summarize_run_degradation(
            {}, artifacts={"paper_skeleton": {"review_notes": ["Skeleton generation failed: boom"]}}
        )
        assert out["status"] == ad.STATUS_DEGRADED
        assert out["stages"] == ["paper_skeleton"]

    def test_component_graph_no_components_is_not_ai_failure(self):
        out = ad.summarize_run_degradation(
            {}, artifacts={"component_graph": {"validation_issues": [_issue("component_graph_failed", "no components")]}}
        )
        assert out["status"] == ad.STATUS_OK
        assert out["facts"] == []

    def test_component_graph_llm_failure_is_degraded(self):
        out = ad.summarize_run_degradation(
            {}, artifacts={"component_graph": {"validation_issues": [_issue("component_graph_failed")]}}
        )
        assert out["status"] == ad.STATUS_DEGRADED

    def test_item_level_skipped_reason_is_partial(self):
        artifacts = {"contextual_explanation": {"explanations": [
            {"skipped_reason": "llm_call_failed"}, {"skipped_reason": None},
        ]}}
        out = ad.summarize_run_degradation({}, artifacts=artifacts)
        assert out["status"] == ad.STATUS_DEGRADED
        assert out["details"]["stages"]["contextual_explanation"]["kind"] == "partial"

    def test_limit_skips_are_not_degradation(self):
        """上限・素材なしの取りこぼしは参照の健全性が言う（二箇所で言わない）。"""
        out = ad.summarize_run_degradation(
            {"contextual_explanation": {"skipped_by_limit": True}},
            artifacts={"discuss_opening": {"skipped_reason": "no_source_material"}},
        )
        assert out["status"] == ad.STATUS_OK

    def test_llm_failures_block_note(self):
        so = {"llm_failures": {"note": "解析中に AI 提供元の呼び出しが失敗しました。", "stages": ["dsl_linking"],
                               "failed_calls": 12, "rate_limited": False}}
        out = ad.summarize_run_degradation(so, artifacts={})
        assert out["status"] == ad.STATUS_DEGRADED
        assert "dsl_linking" in out["stages"]
        assert _no_digits(out["facts"])
        assert out["details"]["llm_failures"]["failed_calls"] == 12


class TestStatusAndSnapshot:
    def test_clean_artifacts_ok(self):
        out = ad.summarize_run_degradation({}, artifacts={"thesis_reconstruction": {"validation_issues": []}})
        assert out == {"status": "ok", "facts": [], "stages": [], "details": {}}

    def test_no_artifacts_no_snapshot_unchecked(self):
        assert ad.summarize_run_degradation({})["status"] == ad.STATUS_UNCHECKED
        assert ad.summarize_run_degradation(None)["status"] == ad.STATUS_UNCHECKED

    def test_light_signal_without_artifacts_is_degraded(self):
        out = ad.summarize_run_degradation({"component_assembly": {"fallback": True, "fallback_component_count": 4}})
        assert out["status"] == ad.STATUS_DEGRADED

    def test_snapshot_used_when_no_artifacts(self):
        snap = {"status": "degraded", "facts": ["「概念グラフへの接続」は AI の応答が得られず、決定論の代替で作られています。"],
                "stages": ["dsl_linking"], "details": {}}
        out = ad.summarize_run_degradation({ad.DEGRADATION_KEY: snap})
        assert out["status"] == "degraded" and out["stages"] == ["dsl_linking"]

    def test_hydrated_artifacts_preferred_over_snapshot(self):
        so = {ad.DEGRADATION_KEY: {"status": "ok", "facts": []},
              "_artifacts": {"dsl_linking": {"validation_issues": [_issue("dsl_linking_failed")]}}}
        assert ad.summarize_run_degradation(so)["status"] == ad.STATUS_DEGRADED

    def test_never_raises(self):
        out = ad.summarize_run_degradation({"_artifacts": {"x": {"validation_issues": "broken"}}})
        assert out["status"] in ad.STATUSES

    def test_public_view_drops_details_and_unchecked_facts(self):
        view = ad.public_view({"status": "degraded", "facts": ["a"], "details": {"n": 3}, "stages": ["x"]})
        assert view == {"status": "degraded", "facts": ["a"]}
        assert ad.public_view({"status": "bogus"}) == {"status": "unchecked", "facts": []}

    def test_all_fact_templates_have_no_digits(self):
        for stage in ("thesis_reconstruction", "dsl_linking", "component_assembly", "component_graph",
                      "paper_skeleton", "figure_table_semantics", "blueprint"):
            for kind in ad.KINDS:
                assert _no_digits([ad.fact_for(stage, kind)])
        assert _no_digits([ad.BANNER_PREFIX, ad.FACT_UNCHECKED])

    def test_every_rule_id_is_written_by_an_agent(self):
        """表の rule_id は実在の agent ソースに現れる（語彙のずれを防ぐ）。"""
        agents = BACKEND.parent / "src" / "episteme_graph" / "agents"
        corpus = "\n".join(p.read_text(encoding="utf-8") for p in agents.rglob("*.py") if "/tests/" not in str(p))
        for rule_id in ad.FALLBACK_RULE_KINDS:
            assert f'"{rule_id}"' in corpus, rule_id
