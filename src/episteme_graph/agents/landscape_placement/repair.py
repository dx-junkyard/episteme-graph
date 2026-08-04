"""Repair hooks for LandscapePlacementAgent.

The retry loop itself is **not** reimplemented here: it is
``core/llm_worker/repair.run_with_repair`` (1 initial call + up to 2 repair
attempts), shared with tension / structure_anchor / reconstruction /
doubt.scope_candidates / doubt.assumption_mining /
deliberation.standardization / discuss_opening.

This module only supplies the two domain-specific hooks that
``run_with_repair`` injects:

- :func:`build_repair_prompt` — what to append to the base prompt on retry.
- :func:`build_repair_failed_result` — what to return when the ceiling is hit.

Per design §7.2, exhausting the repair budget means **no rows are written**
(``placements=[]`` with ``skipped_reason='repair_failed'``): an invented
placement on the reference map is worse than no placement. The failure is
recorded honestly rather than dropped (P4).
"""
from __future__ import annotations

from .schema import (
    LandscapePlacementInput,
    LandscapePlacementResult,
    SKIPPED_REASON_REPAIR_FAILED,
    ValidationIssue,
)

MAX_ERRORS_IN_PROMPT = 12


def build_repair_prompt(previous_raw: str, errors: list[str]) -> str:
    """直前の生出力と検証エラーを添えた修復指示（追記文）。

    指示は validator の hard error と矛盾させない: 素材が渡されている状態で
    ``placements`` を空にしたまま ``unplaced_domains`` も書かないと
    ``no_placement_and_no_unplaced_declaration`` の hard error になり、修復が必ず
    失敗して LLM の1周が無駄になる。よってここでは「置けないなら
    ``unplaced_domains`` に理由を書く」という**空にしてよい正しい形**を明示する
    （LS10: 配置不能は失敗ではなく信号）。
    """
    listed = "\n".join(f"- {e}" for e in (errors or [])[:MAX_ERRORS_IN_PROMPT])
    return (
        "## 直前の出力\n"
        f"{previous_raw or '(出力なし / JSON として読めなかった)'}\n\n"
        "## 検証エラー\n"
        f"{listed}\n\n"
        "上記のエラーをすべて直した JSON を返してください。特に:\n"
        "- `domain_key` / `node_id` は**提示された一覧の文字列をそのままコピー**する"
        "（創作しない・別ドメインの node_id を混ぜない）。\n"
        "- `evidence_quote` は素材（paper / claims）の文字列を**そのままコピー**する"
        "（翻訳・要約・言い換えをしない）。\n"
        "- `weight` は 0.0〜1.0 の数値を必ず入れる。`perspective` は指定語彙から選ぶ。\n"
        "- `reason` は日本語の事実文で必ず書く。\n"
        "- どの領域にも置けないなら `placements` を空配列にし、**その理由を"
        "`unplaced_domains` に domain ごとに書く**（理由なしで空にはしない）。\n"
        "JSON のみを返してください。"
    )


def build_repair_failed_result(
    item: LandscapePlacementInput,
    errors: list[str],
    *,
    cartridge_id: str | None = None,
) -> LandscapePlacementResult:
    """修復上限に達したときの結果（配置なし・理由付きで保持）。"""
    issues = [
        ValidationIssue("repair_failed", "error", message)
        for message in (errors or [])[:MAX_ERRORS_IN_PROMPT]
    ]
    return LandscapePlacementResult.make_skipped(
        item,
        SKIPPED_REASON_REPAIR_FAILED,
        "validation failed after repair attempts; no placement generated",
        cartridge_id=cartridge_id,
        validation_issues=issues,
    )
