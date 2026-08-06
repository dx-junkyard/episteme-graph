"""Prompt input preparation for LandscapePlacementAgent.

Design: ``docs/features/knowledge_landscape_design.md`` §7.3.

The caller (``core/landscape/builder.py``) has already resolved every opaque id
into text and read the **frozen** skeletons, so this module's job is narrow:

1. Bound each text so one pathological document cannot blow the prompt budget.
2. Bound the number of items per section (claims / domains / nodes per domain).
3. Provide the whitespace-normalized haystack the validator uses to check that
   ``evidence_quote`` was really copied from the material (LS4 の捏造ガード).
"""
from __future__ import annotations

import re

from .schema import LandscapePlacementInput

# Defensive prompt-size bounds (generous on purpose — guard pathological
# inputs, do not compress normal ones). The astrophysics v0.1 skeleton has 10
# regions + ~48 concepts = ~58 nodes, so 120 leaves plenty of headroom.
MAX_CLAIMS = 20
MAX_DOMAINS = 12
MAX_NODES_PER_DOMAIN = 120
MAX_CLAIM_CHARS = 400
MAX_THESIS_CHARS = 800
MAX_TITLE_CHARS = 300

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
    連続空白だけが LLM 側で崩れやすいので、そこだけ吸収する
    （``discuss_opening/input_builder.py`` と同じ規則）。
    """
    return _WS_RE.sub(" ", str(text or "")).strip()


class LandscapePlacementInputBuilder:
    """Bounds a :class:`LandscapePlacementInput` for the prompt (never mutates it)."""

    def prepare_for_prompt(self, item: LandscapePlacementInput) -> dict:
        return {
            "paper": {
                "title": truncate(item.paper_title, MAX_TITLE_CHARS),
                "central_question": truncate(item.central_question, MAX_THESIS_CHARS),
                "central_thesis": truncate(item.central_thesis, MAX_THESIS_CHARS),
                "paper_goal": truncate(item.paper_goal, MAX_THESIS_CHARS),
                "headline_claim": truncate(item.headline_claim, MAX_THESIS_CHARS),
            },
            "claims": [
                {"claim_id": c.claim_id, "text": truncate(c.text, MAX_CLAIM_CHARS)}
                for c in item.claim_summaries[:MAX_CLAIMS]
                if c.text
            ],
            "domains": [
                {
                    "domain_key": d.domain_key,
                    "domain_name": d.domain_name,
                    "nodes": [
                        {
                            "node_id": n.node_id,
                            "label": n.label,
                            "kind": n.kind,
                            "region_id": n.region_id,
                        }
                        for n in d.nodes[:MAX_NODES_PER_DOMAIN]
                    ],
                }
                for d in item.domains[:MAX_DOMAINS]
                if d.nodes
            ],
        }

    def quote_haystack(self, item: LandscapePlacementInput) -> str:
        """``evidence_quote`` の verbatim 照合に使う正規化済みテキストの連結。

        プロンプトに載せた（＝切り詰めた後の）テキストではなく**元のテキスト**を
        使う: 切り詰め前の全文から引用されていれば grounding としては正しく、
        「切り詰めたせいで正しい引用が弾かれる」誤検出を避ける。
        """
        return " ␟ ".join(
            normalize_for_quote_match(text) for text in item.source_texts() if text
        )

    @staticmethod
    def node_index(item: LandscapePlacementInput):
        """``(domain_key, node_id) -> node``（validator / builder が共有する索引）。"""
        return item.node_index()

    @staticmethod
    def claim_ids(item: LandscapePlacementInput) -> set[str]:
        return item.claim_ids()
