"""Prompt input preparation for DiscussOpeningAgent.

Design: ``docs/features/discuss_opening_authoring_design.md`` §4.1.

The caller (``core/discuss/authoring.py``, driven by the orchestrator) has
already resolved every opaque id into text, so this module's job is narrow:

1. Bound each text so one pathological document cannot blow the prompt budget.
2. Bound the number of items per section (assumptions / author choices).
3. Provide the whitespace-normalized haystack used by the validator to check
   that ``evidence_quote`` was really copied from the material (grounding —
   DM1 "論文の内容として提示するものは論文まで辿れること").
"""
from __future__ import annotations

import re

from .schema import DiscussOpeningInput

# Defensive prompt-size bounds (generous on purpose — guard pathological
# inputs, do not compress normal ones).
MAX_ASSUMPTIONS = 12
MAX_AUTHOR_CHOICES = 12
MAX_ITEM_CHARS = 400
MAX_THESIS_CHARS = 800

_TRUNCATION_MARK = "…"
_WS_RE = re.compile(r"\s+")


def truncate(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + _TRUNCATION_MARK


def normalize_for_quote_match(text: str) -> str:
    """引用照合用の正規化（空白の畳み込みのみ。翻訳・大文字小文字は触らない）。

    英語論文由来の原文をそのまま引用させるため、記号や語形は変えない。改行や
    連続空白だけが LLM 側で崩れやすいので、そこだけ吸収する。
    """
    return _WS_RE.sub(" ", str(text or "")).strip()


class DiscussOpeningInputBuilder:
    """Bounds a :class:`DiscussOpeningInput` for the prompt (never mutates it)."""

    def prepare_for_prompt(self, item: DiscussOpeningInput) -> dict:
        return {
            "language": item.language,
            "thesis": {
                "central_question": truncate(item.thesis.central_question, MAX_THESIS_CHARS),
                "paper_goal": truncate(item.thesis.paper_goal, MAX_THESIS_CHARS),
                "central_thesis_text": truncate(
                    item.thesis.central_thesis_text, MAX_THESIS_CHARS
                ),
            },
            "untested_assumptions": [
                {
                    "statement": truncate(a.statement, MAX_ITEM_CHARS),
                    "target_type": a.target_type,
                    "verification_status": a.verification_status,
                }
                for a in item.untested_assumptions[:MAX_ASSUMPTIONS]
            ],
            "author_choices": [
                {
                    "operation": c.operation,
                    "operation_subtype": c.operation_subtype,
                    "reason": truncate(c.reason, MAX_ITEM_CHARS),
                    "from": truncate(c.from_label, MAX_ITEM_CHARS),
                    "to": truncate(c.to_label, MAX_ITEM_CHARS),
                }
                for c in item.author_choices[:MAX_AUTHOR_CHOICES]
            ],
        }

    def quote_haystack(self, item: DiscussOpeningInput) -> str:
        """``evidence_quote`` の verbatim 照合に使う正規化済みテキストの連結。

        プロンプトに載せた（＝切り詰めた後の）テキストではなく**元のテキスト**を
        使う: 切り詰め前の全文から引用されていれば grounding としては正しく、
        「切り詰めたせいで正しい引用が弾かれる」誤検出を避ける。
        """
        return " ␟ ".join(
            normalize_for_quote_match(text) for text in item.source_texts() if text
        )
