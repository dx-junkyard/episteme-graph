"""パイプラインステージ宣言（`_PIPELINE_STEPS`）の単一正本性を固定する構造テスト。

提案 §2-9: 「このステージは LLM を呼ぶのか」の判定はかつて `LLM_STAGE_NAMES` の
リテラル定義 / `report_start(..., unit=)` の指定 / docs の種別列の3箇所に散っており、
互いに食い違っていた（docs/architecture/doc_review_findings_2026-08-13.md 7-1）。
現在は `PipelineStageDef` の `llm_kind` / `model_policy` / `progress_unit` /
`vision_optional` が事実を宣言し、判定用の集合はすべてそこから導出する。

本テストは①導出結果が従来のリテラル集合と一致し続けること（回帰検出。期待値は
テスト側にリテラルで持つ）②宣言と実呼び出し（`report_start` の unit）がズレないこと
③意図的な例外（M層対象外だが LLM を呼ぶ component_graph）が1件のまま増えないこと
を固定する。DB・LLM・FastAPI には触らない。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_SRC_DIR = BACKEND.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from core.document_pipeline import orchestrator as orch  # noqa: E402

_ORCHESTRATOR_SOURCE = (
    BACKEND / "core" / "document_pipeline" / "orchestrator.py"
).read_text(encoding="utf-8")

# 現行の M層対象ステージ（= `model_policy=True`）。導出化の前後で1件も変わらないことを
# 固定するため、期待値はテスト側にリテラルで置く（実装側の導出をそのまま写さない）。
EXPECTED_LLM_STAGE_NAMES = {
    "paper_skeleton",
    "rhetorical_role",
    "claim_qualification",
    "equation_semantics",
    "apparatus_semantics",
    "thesis_reconstruction",
    "dsl_linking",
    "component_assembly",
    "narrative_annotator",
    "contextual_explanation",
    "discuss_opening",
    "landscape_placement",
}

# LLM を呼ぶが M層のステージ別選択・使用モデル記録の対象外になっているステージ。
# 昇格は API 応答（`GET /pipeline-stages`）が変わる挙動変更なのでオーナー判断待ち。
EXPECTED_MODEL_POLICY_EXCLUSIONS = {"component_graph"}


def _named_steps() -> list:
    return [step for step in orch._PIPELINE_STEPS if step.name]


class TestDerivedStageSets:
    def test_llm_stage_names_matches_expected_literal_set(self):
        """`LLM_STAGE_NAMES` は導出になったが集合は不変（12件）。"""
        assert set(orch.LLM_STAGE_NAMES) == EXPECTED_LLM_STAGE_NAMES
        assert len(orch.LLM_STAGE_NAMES) == 12
        # 後方互換エイリアスは同一オブジェクトを指し続ける。
        assert orch._LLM_STAGE_NAMES == orch.LLM_STAGE_NAMES

    def test_model_policy_is_subset_of_llm_calling_with_one_known_exclusion(self):
        """M層対象 ⊆ 実際に LLM を呼ぶステージ。差分は既知の1件だけ。"""
        assert orch.LLM_STAGE_NAMES <= orch.LLM_CALLING_STAGE_NAMES
        assert (
            set(orch.LLM_CALLING_STAGE_NAMES) - set(orch.LLM_STAGE_NAMES)
            == EXPECTED_MODEL_POLICY_EXCLUSIONS
        )

    def test_vision_stage_names(self):
        """vision LLM を主呼び出しにするステージは apparatus_semantics のみ。"""
        assert set(orch.VISION_STAGE_NAMES) == {"apparatus_semantics"}
        # 条件付き vision（equation_semantics）は VISION_STAGE_NAMES に混ぜない。
        optional = {s.name for s in _named_steps() if s.vision_optional}
        assert optional == {"equation_semantics"}
        assert not (optional & set(orch.VISION_STAGE_NAMES))

    def test_steps_cover_pipeline_stages_exactly(self):
        """名前付きステップ集合 == PIPELINE_STAGES - 終端マーカー。"""
        assert {s.name for s in _named_steps()} == set(orch.PIPELINE_STAGES) - {"completed"}

    def test_hooks_declare_no_model_usage(self):
        """between-stage フック（name=None）はモデルを呼ばず M層対象でもない。"""
        hooks = [step for step in orch._PIPELINE_STEPS if step.name is None]
        assert hooks, "expected between-stage hooks in _PIPELINE_STEPS"
        for hook in hooks:
            assert hook.llm_kind == orch.LLM_KIND_NONE
            assert hook.model_policy is False
            assert hook.progress_unit is None

    def test_declared_vocabularies_are_closed(self):
        """llm_kind / progress_unit は定数集合の語彙のみを使う。"""
        for step in orch._PIPELINE_STEPS:
            assert step.llm_kind in orch.LLM_KINDS, step.name
            assert step.progress_unit is None or step.progress_unit in orch.PROGRESS_UNITS, step.name


class TestProgressUnitDeclarations:
    """`progress_unit` の宣言が実際の `report_start(...)` 呼び出しと一致すること。"""

    @staticmethod
    def _report_start_units() -> dict[str, str]:
        # report_start は1行で呼ばれる（`total=len(...)` のように括弧が入れ子になるため
        # `[^)]` では途中で打ち切られる）。行末までを走査対象にする。
        pattern = re.compile(
            r'report_start\(\s*"(?P<stage>[a-z_]+)"[^\n]*?unit="(?P<unit>[a-z_]+)"'
        )
        return {m.group("stage"): m.group("unit") for m in pattern.finditer(_ORCHESTRATOR_SOURCE)}

    def test_units_match_actual_report_start_calls(self):
        actual = self._report_start_units()
        # 抽出が壊れていないことの下限チェック（正規表現の空振りで緑にならないように）。
        assert len(actual) >= 20
        for step in _named_steps():
            assert step.progress_unit == actual.get(step.name), (
                f"stage {step.name}: declared progress_unit={step.progress_unit!r} "
                f"but report_start unit={actual.get(step.name)!r}"
            )

    def test_llm_call_unit_implies_an_llm_calling_stage(self):
        """unit="llm_call" を宣言するのは実際に LLM を呼ぶステージだけ。

        逆向き（LLM ステージが必ず llm_call）は成り立たない（rhetorical_role="blocks" 等、
        unit は入力単位の意味論）ので要求しない。
        """
        llm_call_stages = {s.name for s in _named_steps() if s.progress_unit == "llm_call"}
        assert llm_call_stages, "expected at least one stage reporting unit='llm_call'"
        assert llm_call_stages <= set(orch.LLM_CALLING_STAGE_NAMES)
