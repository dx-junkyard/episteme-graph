"""``agents/coverage_report.py``（取りこぼし報告の共通形式）の単体テスト。

正本: `docs/architecture/knowledge_structure_review_2026-09-12.md` §4 Phase 0 の
P0-10（F-18「取りこぼしの量がどこにも報告されない」）。
"""
from __future__ import annotations

import ast
from pathlib import Path

from episteme_graph.agents.coverage_report import (
    COVERAGE_REPORT_KEY,
    COVERAGE_REQUIRED_KEYS,
    build_coverage_report,
    is_coverage_report,
)


# --- build_coverage_report -------------------------------------------------

def test_truncated_is_derived_not_declared():
    """打ち切り数は必ず母集合 - 処理数（呼び出し側が申告できない）。"""
    report = build_coverage_report(population=936, processed=64)
    assert report["population"] == 936
    assert report["processed"] == 64
    assert report["truncated"] == 872


def test_required_keys_always_present():
    report = build_coverage_report(population=0, processed=0)
    for key in COVERAGE_REQUIRED_KEYS:
        assert key in report
    assert report["truncated"] == 0
    assert report["reasons"] == []


def test_no_truncation_still_reports():
    """打ち切りが無くても報告は省略しない（「見た」ことの記録）。"""
    report = build_coverage_report(population=12, processed=12, unit="figures")
    assert report["truncated"] == 0
    assert report["unit"] == "figures"


def test_reasons_are_deduped_and_ordered():
    report = build_coverage_report(
        population=10,
        processed=3,
        reasons=["max_items", "skipped_by_limit", "max_items", "  ", "", None],
    )
    assert report["reasons"] == ["max_items", "skipped_by_limit"]


def test_reasons_dropped_when_nothing_was_truncated():
    """取りこぼしが無いのに理由だけ残ると読み手を誤らせる。"""
    report = build_coverage_report(population=5, processed=5, reasons=["max_items"])
    assert report["reasons"] == []


def test_processed_greater_than_population_is_clamped_not_negative():
    report = build_coverage_report(population=3, processed=9)
    assert report["processed"] == 3
    assert report["truncated"] == 0


def test_negative_inputs_are_floored_at_zero():
    report = build_coverage_report(population=-5, processed=-1)
    assert report == {"population": 0, "processed": 0, "truncated": 0, "reasons": []}


def test_none_inputs_do_not_raise():
    report = build_coverage_report(population=None, processed=None)  # type: ignore[arg-type]
    assert report["population"] == 0


def test_unit_and_details_are_optional_keys():
    bare = build_coverage_report(population=1, processed=1)
    assert "unit" not in bare and "details" not in bare
    rich = build_coverage_report(
        population=2,
        processed=1,
        reasons=["max_items"],
        unit="blocks",
        details={"unprocessed_block_ids": ["b7"]},
    )
    assert rich["unit"] == "blocks"
    assert rich["details"] == {"unprocessed_block_ids": ["b7"]}


def test_details_are_copied_not_aliased():
    details = {"keys": ["a"]}
    report = build_coverage_report(population=2, processed=1, details=details)
    details["keys"].append("b")
    # 浅いコピーなので dict 自体は独立（呼び出し側の再代入で報告が変わらない）。
    details["added"] = True
    assert "added" not in report["details"]


# --- is_coverage_report ----------------------------------------------------

def test_is_coverage_report_accepts_built_reports():
    assert is_coverage_report(build_coverage_report(population=4, processed=1))
    assert is_coverage_report(
        build_coverage_report(population=4, processed=1, reasons=["x"], unit="u")
    )


def test_is_coverage_report_rejects_other_shapes():
    assert not is_coverage_report(None)
    assert not is_coverage_report({})
    assert not is_coverage_report({"population": 3, "processed": 1})
    # truncated が導出値と合わない（自前で組み立てた dict）
    assert not is_coverage_report(
        {"population": 3, "processed": 1, "truncated": 0, "reasons": []}
    )
    # reasons が list でない
    assert not is_coverage_report(
        {"population": 3, "processed": 1, "truncated": 2, "reasons": "max_items"}
    )
    # 数値でない
    assert not is_coverage_report(
        {"population": "many", "processed": 1, "truncated": 2, "reasons": []}
    )


def test_coverage_report_key_is_the_shared_key_name():
    assert COVERAGE_REPORT_KEY == "coverage"


# --- モジュールの純粋性 -----------------------------------------------------

def test_module_imports_stdlib_only():
    """全ステージ（src / backend の両方）から読める形式の正本なので、
    ドメイン層・外部ライブラリに依存させない。"""
    source = Path(
        __import__("episteme_graph.agents.coverage_report", fromlist=["__file__"]).__file__
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imported.append(node.module.split(".")[0])
            elif node.level:
                imported.append("<relative>")
    allowed = {"__future__", "typing", "dataclasses", "collections", "json", "math"}
    assert set(imported) <= allowed, f"unexpected imports: {sorted(set(imported) - allowed)}"
