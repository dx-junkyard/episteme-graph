"""Repair hooks for DiscussOpeningAgent.

The retry loop itself is **not** reimplemented here: it is
``core/llm_worker/repair.run_with_repair`` (1 initial call + up to 2 repair
attempts), shared with tension / structure_anchor / reconstruction /
doubt.scope_candidates / doubt.assumption_mining / deliberation.standardization.
This module only supplies the two domain-specific hooks that
``run_with_repair`` injects:

- :func:`build_repair_prompt` — what to append to the base prompt on retry.
- :func:`build_repair_failed_result` — what to return when the ceiling is hit.

Per design §4.1, exhausting the repair budget means **nothing is generated**
(``seeds=[]`` with ``skipped_reason='repair_failed'``): an ungrounded or
fabricated discussion starter is worse than no discussion starter. The failure
is recorded honestly rather than dropped (P4).
"""
from __future__ import annotations

from .schema import (
    DiscussOpeningInput,
    DiscussOpeningResult,
    SKIPPED_REASON_REPAIR_FAILED,
    ValidationIssue,
)

MAX_ERRORS_IN_PROMPT = 12


def build_repair_prompt(previous_raw: str, errors: list[str]) -> str:
    """直前の生出力と検証エラーを添えた修復指示（追記文）。

    指示は validator の hard error と矛盾させない（2026-08-01, [D-7]）: 素材
    （未検証前提 / 著者の選んだ手）が渡されている状態で ``seeds`` を空にすると
    ``empty_seeds_with_material`` の hard error になり、修復が必ず失敗して LLM の
    1周が無駄になる。よってここでは「空にしてよい」とは言わず、**素材の範囲内で
    最低1件返す**ことを求める（素材そのものが無い document には
    ``DiscussOpeningInput.has_material()`` の縮退でそもそも到達しない。素材が不透明
    ID だけのケースも ``core/discuss/authoring.py`` 側で除外済み）。
    """
    listed = "\n".join(f"- {e}" for e in (errors or [])[:MAX_ERRORS_IN_PROMPT])
    return (
        "## 直前の出力\n"
        f"{previous_raw or '(出力なし / JSON として読めなかった)'}\n\n"
        "## 検証エラー\n"
        f"{listed}\n\n"
        "上記のエラーをすべて直した JSON を返してください。特に:\n"
        "- `evidence_quote` は素材の文字列を**そのままコピー**する"
        "（翻訳・要約・言い換えをしない）。\n"
        "- `body` は「？」または「?」で終わる疑問文にする。\n"
        "- 素材に無いことは書かない。ただし素材は渡されているので `seeds` は"
        "空にしない — 確実に裏付けられる1件だけでもよいので、`evidence_quote` が"
        "素材の文字列と一致する seed を最低1件返す。\n"
        "JSON のみを返してください。"
    )


def build_repair_failed_result(
    item: DiscussOpeningInput,
    errors: list[str],
    *,
    cartridge_id: str | None = None,
) -> DiscussOpeningResult:
    """修復上限に達したときの結果（生成なし・理由付きで保持）。"""
    issues = [
        ValidationIssue("repair_failed", "error", message)
        for message in (errors or [])[:MAX_ERRORS_IN_PROMPT]
    ]
    return DiscussOpeningResult.make_skipped(
        item,
        SKIPPED_REASON_REPAIR_FAILED,
        "validation failed after repair attempts; no discussion seed generated",
        cartridge_id=cartridge_id,
        validation_issues=issues,
    )
