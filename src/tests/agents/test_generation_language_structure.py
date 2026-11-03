"""生成言語の指定で component の構造が変わらないこと（IK-0571 §6）。

component の ``summary`` を生成言語の対象に加える前提として、summary を英語のキーワードで
読んで構造を導いていた後段 3 箇所を「宣言された語彙値（operation / primary_operation /
responsibility_type）を先に読む」形に寄せた。

- 語彙値を宣言した component では、summary の言語（英語 / 日本語）に依らず結果が同じ。
- 語彙値を宣言していない component では従来の本文照合のまま（従来入力の結果は不変 —
  実論文 3 本の component_assembly artifact で旧式と突き合わせる）。
- figure_table_semantics の teaching_takeaway は決定論のテンプレートで、未指定なら従来の英語。
"""
from __future__ import annotations

import ast
import glob
import inspect
import json
from pathlib import Path

import pytest

from episteme_graph.agents.component_assembly import claim_centered_planner as planner
from episteme_graph.agents.component_assembly import component_refiner as refiner
from episteme_graph.agents.component_assembly import enrichment
from episteme_graph.agents.component_assembly import granularity_analyzer
from episteme_graph.agents.component_assembly.schema import (
    ComponentAssemblyResult,
    ComponentRecord,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = sorted(glob.glob(str(ROOT / "backend/tests/fixtures/theory_modules/*.json")))

EN = "We eliminate the nuisance parameter and derive the final consistency relation as a result."
JA = "迷惑パラメータを消去し、最終的な整合関係を結果として導く。"


def _component(**kwargs) -> ComponentRecord:
    base = dict(
        component_id="c1", component_type="RelationComponent", label="Relation", summary="",
        inputs=[], outputs=[], preconditions=[], cautions=[], dependencies=[],
        evidence_refs={}, reason="", confidence=0.5, review_notes=[],
    )
    base.update(kwargs)
    return ComponentRecord(**base)


# ── 旧式（変更前の本文照合）— 語彙値を宣言しない入力では新式と一致しなければならない ──

def _old_flow_relation(c) -> str:
    text = " ".join([c.label, c.summary, c.reason]).lower()
    if "eliminat" in text:
        return "eliminate_parameter"
    if "consistency" in text or "constraint" in text or "residual" in text:
        return "derive_constraint"
    if "diagnos" in text or "forecast" in text:
        return "diagnose_with"
    if "correct" in text:
        return "apply_correction"
    return "derive_from"


def _old_infer_operation(c) -> str:
    text = " ".join([str(c.label or ""), str(c.summary or ""), str(c.reason or "")]).lower()
    for needle, family in refiner._OPERATION_FAMILY_RULES:
        if needle in text:
            return family
    return ""


def _old_support_role(c) -> str:
    text = planner._component_text(c)
    responsibility = str(c.responsibility_type or "").lower()
    operation = str(c.operation or c.primary_operation or "").lower()
    if responsibility == "limitation" or any(k in text for k in ("limitation", "caveat", "invalid")):
        return "limitation"
    if responsibility == "application" or any(k in operation + " " + text for k in ("forecast", "diagnos", "application")):
        return "application"
    if responsibility == "constraint" or any(k in text for k in ("consistency relation", "constraint", "final relation", "result")):
        return "result"
    if responsibility == "derivation" or any(k in operation + " " + text for k in ("eliminat", "derive", "solve")):
        return "derivation_core"
    if responsibility in {"observation_model", "observable_basis", "equation_system"}:
        return "observable_bridge"
    return "theory_base"


def _fixture_components():
    out = []
    for path in FIXTURES:
        data = json.load(open(path))
        ca = dict(data["artifacts"]["component_assembly"])
        ca.setdefault("document_id", data["document_id"])
        out.extend(ComponentAssemblyResult.from_dict(ca).components)
    return out


def test_fixtures_present():
    assert len(FIXTURES) == 3 and _fixture_components()


@pytest.mark.parametrize("fn,old", [
    (enrichment._flow_relation, _old_flow_relation),
    (refiner._infer_operation_from_text, _old_infer_operation),
    (planner._infer_support_role, _old_support_role),
])
def test_undeclared_components_keep_previous_results_on_real_artifacts(fn, old):
    """語彙値を宣言していない component（実論文 3 本の artifact）では旧式と同じ結果。"""
    compared = 0
    for c in _fixture_components():
        if enrichment.declared_operation_text(c) or c.responsibility_type:
            continue
        assert fn(c) == old(c), c.component_id
        compared += 1
    assert compared > 0


@pytest.mark.parametrize("declared", [
    dict(operation="eliminate"), dict(operation="constrain"), dict(operation="diagnose"),
    dict(operation="derive"), dict(primary_operation="apply_correction"), dict(operation="define"),
])
def test_flow_relation_is_language_independent_when_operation_declared(declared):
    en = enrichment._flow_relation(_component(summary=EN, **declared))
    ja = enrichment._flow_relation(_component(summary=JA, **declared))
    blank = enrichment._flow_relation(_component(summary="", **declared))
    assert en == ja == blank


@pytest.mark.parametrize("primary", ["linearize the system", "eliminate", "solve", "define"])
def test_operation_fallback_reads_primary_operation_not_summary(primary):
    en = refiner._infer_operation_from_text(_component(summary=EN, primary_operation=primary))
    ja = refiner._infer_operation_from_text(_component(summary=JA, primary_operation=primary))
    assert en == ja == refiner._operation_family(primary)


@pytest.mark.parametrize("declared", [
    dict(responsibility_type="derivation"), dict(responsibility_type="limitation"),
    dict(responsibility_type="definition"), dict(responsibility_type="observable_basis"),
    dict(operation="forecast"), dict(operation="eliminate"), dict(operation="define"),
])
def test_support_role_is_language_independent_when_declared(declared):
    en = planner._infer_support_role(_component(summary=EN, **declared))
    ja = planner._infer_support_role(_component(summary=JA, **declared))
    assert en == ja


def test_undeclared_component_still_reads_text():
    """宣言の無い component は従来どおり本文から（情報を落とさない）。"""
    assert enrichment._flow_relation(_component(summary=EN)) == "eliminate_parameter"
    assert planner._infer_support_role(_component(summary="known limitation")) == "limitation"


def test_dead_summary_readers_are_removed():
    """summary を英語キーワードで読むだけの未使用関数を残さない。"""
    assert not hasattr(refiner, "_component_text")
    assert not hasattr(granularity_analyzer, "_component_text")


def _first_attribute_line(fn, attr: str) -> int:
    tree = ast.parse(inspect.getsource(fn))
    lines = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == attr]
    return min(lines) if lines else 10**9


@pytest.mark.parametrize("fn,declared_attr", [
    (enrichment._flow_relation, "declared_operation_text"),
    (refiner._infer_operation_from_text, "primary_operation"),
])
def test_declared_vocabulary_is_read_before_summary(fn, declared_attr):
    src = inspect.getsource(fn)
    first_summary = _first_attribute_line(fn, "summary")
    assert declared_attr in src
    declared_line = min(
        i + 1 for i, line in enumerate(src.splitlines()) if declared_attr in line
    )
    assert declared_line < first_summary


# ── figure_table_semantics の teaching_takeaway（決定論のテンプレート）──────────

def test_figure_table_takeaway_unset_is_unchanged_and_ja_is_japanese():
    from episteme_graph.agents.figure_table_semantics.agent import FigureTableSemanticsAgent as A

    caption = "Comparison of current and future constraints"
    assert A._figure_takeaway("comparison_plot", caption, []) == (
        f"Use this comparison_plot to support: {caption[:80]}"
    )
    assert A._figure_takeaway("comparison_plot", caption, [], language=None) == A._figure_takeaway(
        "comparison_plot", caption, []
    )
    ja = A._figure_takeaway("comparison_plot", caption, [], language="ja")
    assert "比較の図" in ja and caption in ja and "Use this" not in ja
    ja_axes = A._table_takeaway("results_summary", caption, ["current_vs_future"], language="ja")
    assert "現在と将来の比較" in ja_axes
    # 空は空のまま（validator の warning の意味を変えない）
    assert A._figure_takeaway("unknown", caption, [], language="ja") == ""


def test_figure_table_run_accepts_language():
    from episteme_graph.agents.figure_table_semantics.agent import FigureTableSemanticsAgent

    param = inspect.signature(FigureTableSemanticsAgent.run).parameters["language"]
    assert param.default is None
