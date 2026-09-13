"""Prompt input preparation for LandscapePlacementAgent.

Design: ``docs/features/knowledge_landscape_design.md`` §7.3.

The caller (``core/landscape/builder.py``) has already resolved every opaque id
into text and read the **frozen** skeletons, so this module's job is narrow:

1. Bound each text so one pathological document cannot blow the prompt budget.
2. Bound the number of items per section (claims / domains / nodes per domain).
3. Provide the whitespace-normalized haystack the validator uses to check that
   ``evidence_quote`` was really copied from the material (LS4 の捏造ガード).
4. Present the skeleton as a **closed world**: ``region → concepts[]`` nested,
   with a deterministic ``concept_slots_remaining`` per region
   (``docs/features/category_gap_candidates_design.md`` §5.1). A flat node list
   cannot say "この領域の概念はこの N 件だけ", which is exactly the judgement the
   category gap candidates need（「領域には当たるが概念が無い」）.
"""
from __future__ import annotations

import re

from .schema import (
    LandscapePlacementInput,
    MAX_CONCEPTS_PER_REGION,
    NODE_KIND_CONCEPT,
    SkeletonNodeOption,
)

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

#: 前段絞り込み（``docs/features/atlas_vector_anchoring_design.md`` §6）が働いた
#: ドメインに添える注記。閉世界の提示を絞った事実を LLM に隠さない（VA7 / VA8）。
#: 文言の正本はここ1箇所（prompt.py は組み立てるだけ）。
PREFILTER_NOTE = (
    "この領域一覧は関連上位への絞り込み提示であり、骨格の全ノードではありません。"
)


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
                self._prepare_domain(d)
                for d in item.domains[:MAX_DOMAINS]
                if d.nodes
            ],
        }

    def _prepare_domain(self, domain) -> dict:
        """1ドメインを ``regions[] → concepts[]`` のネストに組み替える（決定論）。

        ノード数の上限は従来どおりフラットな入力順で切ってから grouping する
        （切り方を変えると同じ骨格に対する提示が run ごとに揺れる）。親領域が
        提示されていない概念は落とさず ``other_concepts`` に残す（P4）。
        """
        nodes: list[SkeletonNodeOption] = list(domain.nodes[:MAX_NODES_PER_DOMAIN])
        regions = [n for n in nodes if n.kind != NODE_KIND_CONCEPT]
        region_ids = {n.node_id for n in regions}

        grouped: dict[str, list[SkeletonNodeOption]] = {}
        orphans: list[SkeletonNodeOption] = []
        for node in nodes:
            if node.kind != NODE_KIND_CONCEPT:
                continue
            if node.region_id in region_ids:
                grouped.setdefault(node.region_id, []).append(node)
            else:
                orphans.append(node)

        prepared = {
            "domain_key": domain.domain_key,
            "domain_name": domain.domain_name,
            "regions": [
                {
                    "node_id": region.node_id,
                    "label": region.label,
                    "kind": region.kind,
                    "concepts": [
                        {"node_id": c.node_id, "label": c.label, "kind": c.kind}
                        for c in grouped.get(region.node_id, ())
                    ],
                    "concept_slots_remaining": max(
                        0,
                        MAX_CONCEPTS_PER_REGION - len(grouped.get(region.node_id, ())),
                    ),
                }
                for region in regions
            ],
        }
        if getattr(domain, "prefiltered", False):
            # 絞り込んだドメインだけに注記を付ける（絞り込んでいないドメインの
            # 「これで全部」という閉世界の言明は従来のまま保つ）。
            prepared["note"] = PREFILTER_NOTE
        if orphans:
            prepared["other_concepts"] = [
                {
                    "node_id": c.node_id,
                    "label": c.label,
                    "kind": c.kind,
                    "region_id": c.region_id,
                }
                for c in orphans
            ]
        return prepared

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

    @staticmethod
    def region_index(item: LandscapePlacementInput):
        """``(domain_key, region_id) -> region node``（gap の親領域検査が使う）。"""
        return item.region_index()

    @staticmethod
    def existing_labels(item: LandscapePlacementInput) -> dict[str, set[str]]:
        """``domain_key -> 既存ノードの正規化ラベル集合``（言い換え申告の検出）。"""
        return item.existing_labels()
