"""全ステージ共通の「取りこぼしの量」報告形式（正本）。

`docs/architecture/knowledge_structure_review_2026-09-12.md` §4 Phase 0 の P0-10。
「入力母集合 / 処理数 / 打ち切り数 / 理由」を1つの dict 形式に固定し、A層 agent の
``summary_stats`` と orchestrator の ``stage_outputs[<stage>]`` の両方が**この関数で**
報告を組み立てる（層ごとに ``truncated_count`` / ``skipped_by_limit`` / ``unplaced_domains``
と形式がばらついていた正直さの実装を一本化する — F-18）。

規約:
- ``truncated`` は必ず ``population - processed``（負にはならない）。呼び出し側が
  「一致した」と申告する形にしない（`decision_context` と同じ導出主義）。
- ``reasons`` は打ち切り・除外の**理由コード**の列（例: ``max_blocks`` /
  ``daily_call_limit`` / ``skipped_by_option``）。数値は載せない — 件数は
  ``population`` / ``processed`` / ``truncated`` の3つで表現し尽くす。
- ステージ固有の内訳（未処理の節・図キーなど）は ``details`` に入れる。
  ``details`` の中身は各ステージの責務で、形式はここでは固定しない。
- 打ち切りが無いとき（``truncated == 0``）でも報告は省略しない（「見た」ことの記録）。

このモジュールは stdlib のみに依存し、src / backend の両方から import できる。
"""

from __future__ import annotations

from typing import Any

COVERAGE_REPORT_KEY = "coverage"
COVERAGE_REQUIRED_KEYS: tuple[str, ...] = ("population", "processed", "truncated", "reasons")


def build_coverage_report(
    *,
    population: int,
    processed: int,
    reasons: list[str] | tuple[str, ...] | None = None,
    unit: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """母集合・処理数から取りこぼし報告を組み立てる。

    ``processed > population`` は呼び出し側のバグなので ``population`` に切り詰め、
    ``truncated`` は 0 にする（報告で例外を投げてステージを止めない）。
    """
    pop = max(int(population or 0), 0)
    done = max(int(processed or 0), 0)
    if done > pop:
        done = pop
    truncated = pop - done
    seen: set[str] = set()
    codes: list[str] = []
    for code in reasons or ():
        text = str(code or "").strip()
        if text and text not in seen:
            seen.add(text)
            codes.append(text)
    if truncated == 0:
        codes = []
    report: dict[str, Any] = {
        "population": pop,
        "processed": done,
        "truncated": truncated,
        "reasons": codes,
    }
    if unit:
        report["unit"] = str(unit)
    if details:
        report["details"] = dict(details)
    return report


def is_coverage_report(value: Any) -> bool:
    """``build_coverage_report`` の形式か（ガードレール・読み手側の判定用）。"""
    if not isinstance(value, dict):
        return False
    if any(key not in value for key in COVERAGE_REQUIRED_KEYS):
        return False
    try:
        pop = int(value["population"])
        done = int(value["processed"])
        trunc = int(value["truncated"])
    except (TypeError, ValueError):
        return False
    return trunc == max(pop - done, 0) and isinstance(value["reasons"], list)
