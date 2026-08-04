"""LandscapePlacementAgent data models.

Design: ``docs/features/knowledge_landscape_design.md`` §7.3 (Phase L1,
migration 065). The agent answers one question per document:

    「この論文は、凍結済みの基準地図のどのアンカーに、どの観点で関係するか」

and it answers it for **all supplied domains in a single LLM call** so the
domains are weighed against each other rather than one at a time.

Invariants carried by this module (design §0):

- LS1 the map is a projection, not a verdict — one paper is *expected* to land
  on several nodes with different ``perspective`` values. Nothing here forces a
  single answer.
- LS3 AI placements stay ``inferred`` — this agent never emits a status. The
  builder (``core/landscape/builder.py``) writes rows as ``inferred``; only a
  teacher can confirm them.
- LS4 evidence-based — every placement carries ``reason`` + ``evidence_quote``,
  and the validator checks the quote is verbatim in the supplied material.
- LS5 no raw numbers to humans — ``weight`` / ``confidence`` live in the DB
  only; API layers convert them to coarse labels.
- LS10 "cannot place" is a signal, not a failure — a domain that does not fit
  is declared in ``unplaced_domains`` with a reason instead of being forced.

The agent never resolves opaque ids: the caller hands over already-resolved
*text* (paper title / thesis / goal / claim texts) plus the skeleton node
inventory. ``backend/core`` is never imported from here except lazily inside
``llm_client`` / ``agent`` (the vocabulary below is duplicated on purpose — the
cross-check with ``core/landscape/schema.py`` is a structural test's job).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from episteme_graph.agents.cartridge_context import CartridgeContext

__all__ = [
    "CartridgeContext",
    "LANDSCAPE_PLACEMENT_VERSION",
    "PERSPECTIVES",
    "PERSPECTIVE_HINTS",
    "NODE_KIND_REGION",
    "NODE_KIND_CONCEPT",
    "NODE_KINDS",
    "DEFAULT_MAX_PLACEMENTS",
    "TARGET_MIN_PLACEMENTS",
    "TARGET_MAX_PLACEMENTS",
    "MAX_REASON_CHARS",
    "MAX_EVIDENCE_QUOTE_CHARS",
    "MIN_EVIDENCE_QUOTE_CHARS",
    "SKIPPED_REASON_NO_MATERIAL",
    "SKIPPED_REASON_NO_DOMAINS",
    "SKIPPED_REASON_LLM_CALL_FAILED",
    "SKIPPED_REASON_REPAIR_FAILED",
    "SkeletonNodeOption",
    "DomainOption",
    "ClaimSummary",
    "LandscapePlacementInput",
    "PlacementCandidate",
    "UnplacedDomain",
    "ValidationIssue",
    "LandscapePlacementResult",
]

LANDSCAPE_PLACEMENT_VERSION = "v1"

# 観点語彙（設計書 §8 の ``core/landscape/schema.py::PERSPECTIVES`` と同値。agent は
# backend/core を import しないため同じリテラルをここにも置き、一致は
# backend/tests のガードレールが構造テストで固定する）。
PERSPECTIVES = (
    "subject",
    "question",
    "method",
    "theory",
    "observation",
    "application",
)

# プロンプト用の短い説明（日本語ラベルの正本は core/landscape/schema.py 側。ここは
# LLM に観点の意味を伝えるためのヒントで、UI 表示には使わない）。
PERSPECTIVE_HINTS = {
    "subject": "この論文が扱う対象そのもの（天体・系・現象）がその領域に属する",
    "question": "この論文が立てた問いがその領域の問いである",
    "method": "この論文が使った方法・装置・解析がその領域のものである",
    "theory": "この論文が依拠・拡張した理論がその領域のものである",
    "observation": "この論文が使った観測・データがその領域のものである",
    "application": "この論文の結果がその領域へ応用・示唆を与える",
}

# 骨格ノードの種別（region = 領域、concept = 領域内の概念）。
NODE_KIND_REGION = "region"
NODE_KIND_CONCEPT = "concept"
NODE_KINDS = (NODE_KIND_REGION, NODE_KIND_CONCEPT)

# LS: 1 document あたりの配置件数上限（既定 8 = ``LANDSCAPE_MAX_PLACEMENTS_PER_DOCUMENT``）。
DEFAULT_MAX_PLACEMENTS = 8
# 設計書 §7.3「複数領域×複数観点への配置が既定」= プロンプトで狙う件数レンジ
# （上限は max_placements で切る。下限は warning にとどめる — 本当に1領域の論文もある）。
TARGET_MIN_PLACEMENTS = 2
TARGET_MAX_PLACEMENTS = 6

# 事実文1〜2文。長い解説にしない（教員のレビュー画面に並ぶ）。
MAX_REASON_CHARS = 400
MAX_EVIDENCE_QUOTE_CHARS = 400
# あまりに短い「引用」は grounding の証明にならない（"the" 等）。
MIN_EVIDENCE_QUOTE_CHARS = 6

# skipped_reason 語彙（P4: 生成しなかったことを必ず理由付きで残す）。
# ``no_frozen_skeleton`` / ``no_source_material`` は設計書 §7.2 のステージ skip 語彙と
# 同じ文字列を使う（builder がそのまま payload に載せられるようにする）。
SKIPPED_REASON_NO_MATERIAL = "no_source_material"
SKIPPED_REASON_NO_DOMAINS = "no_frozen_skeleton"
SKIPPED_REASON_LLM_CALL_FAILED = "llm_call_failed"
SKIPPED_REASON_REPAIR_FAILED = "repair_failed"

# 数値として読めない weight の内部センチネル（validator が hard error にする。
# 黙って 0.5 に丸めない — weight は admin UI の段階ラベルの素なので捏造しない）。
UNPARSED_WEIGHT = -1.0


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _weight_or_sentinel(value: Any) -> float:
    """weight は丸めずに読む（範囲外・非数値は validator が弾く）。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return UNPARSED_WEIGHT
    try:
        return float(value)
    except (TypeError, ValueError):
        return UNPARSED_WEIGHT


@dataclass
class SkeletonNodeOption:
    """配置先候補となる骨格ノード1件（凍結版から解決済み）。"""

    node_id: str
    label: str = ""
    kind: str = NODE_KIND_REGION
    region_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "SkeletonNodeOption":
        d = d or {}
        kind = _clean(d.get("kind")) or NODE_KIND_REGION
        return cls(
            node_id=_clean(d.get("node_id")),
            label=_clean(d.get("label")),
            kind=kind if kind in NODE_KINDS else NODE_KIND_REGION,
            region_id=_clean(d.get("region_id")),
        )


@dataclass
class DomainOption:
    """凍結骨格を持つドメイン1件（配置先の候補集合）。"""

    domain_key: str
    domain_name: str = ""
    nodes: list[SkeletonNodeOption] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "domain_key": self.domain_key,
            "domain_name": self.domain_name,
            "nodes": [n.to_dict() for n in self.nodes],
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "DomainOption":
        d = d or {}
        return cls(
            domain_key=_clean(d.get("domain_key")),
            domain_name=_clean(d.get("domain_name")),
            nodes=[
                SkeletonNodeOption.from_dict(n)
                for n in (d.get("nodes") or [])
                if _clean((n or {}).get("node_id"))
            ],
        )


@dataclass
class ClaimSummary:
    """配置の裏付けに使える claim 1件（解決済みテキスト）。"""

    claim_id: str
    text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ClaimSummary":
        d = d or {}
        return cls(claim_id=_clean(d.get("claim_id")), text=_clean(d.get("text")))


@dataclass
class LandscapePlacementInput:
    """1 document 分の入力（LLM は 1 document = 1 コール・設計書 §7.3）。"""

    document_id: str
    cartridge_id: str | None = None
    paper_title: str = ""
    central_question: str = ""
    central_thesis: str = ""
    paper_goal: str = ""
    headline_claim: str = ""
    claim_summaries: list[ClaimSummary] = field(default_factory=list)
    domains: list[DomainOption] = field(default_factory=list)
    max_placements: int = DEFAULT_MAX_PLACEMENTS

    # -- degradation --------------------------------------------------------

    def has_material(self) -> bool:
        """素材の有無（設計書 §7.2 の ``no_source_material`` 判定）。

        論文タイトルだけでは配置しない（タイトルは根拠として弱く、逐語引用の
        ヘイスタックにも入れていない）。thesis / goal / 問い / headline / claim の
        いずれかがあれば配置を試みる。
        """
        return bool(
            self.central_thesis
            or self.paper_goal
            or self.central_question
            or self.headline_claim
            or any(c.text for c in self.claim_summaries)
        )

    def has_domains(self) -> bool:
        return any(d.nodes for d in self.domains)

    def source_texts(self) -> list[str]:
        """``evidence_quote`` の verbatim 検証に使う入力テキスト全部（順序は決定論）。"""
        texts = [
            t
            for t in (
                self.central_thesis,
                self.paper_goal,
                self.central_question,
                self.headline_claim,
            )
            if t
        ]
        texts.extend(c.text for c in self.claim_summaries if c.text)
        return texts

    def claim_ids(self) -> set[str]:
        return {c.claim_id for c in self.claim_summaries if c.claim_id}

    def node_index(self) -> dict[tuple[str, str], SkeletonNodeOption]:
        """``(domain_key, node_id) -> node``。validator の実在検査と builder の
        ``node_kind`` 解決が同じ索引を使う。"""
        index: dict[tuple[str, str], SkeletonNodeOption] = {}
        for domain in self.domains:
            for node in domain.nodes:
                if domain.domain_key and node.node_id:
                    index.setdefault((domain.domain_key, node.node_id), node)
        return index

    def domain_keys(self) -> list[str]:
        return [d.domain_key for d in self.domains if d.domain_key]

    # -- serialization ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "cartridge_id": self.cartridge_id,
            "paper_title": self.paper_title,
            "central_question": self.central_question,
            "central_thesis": self.central_thesis,
            "paper_goal": self.paper_goal,
            "headline_claim": self.headline_claim,
            "claim_summaries": [c.to_dict() for c in self.claim_summaries],
            "domains": [d.to_dict() for d in self.domains],
            "max_placements": self.max_placements,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "LandscapePlacementInput":
        d = d or {}
        try:
            max_placements = int(d.get("max_placements") or DEFAULT_MAX_PLACEMENTS)
        except (TypeError, ValueError):
            max_placements = DEFAULT_MAX_PLACEMENTS
        return cls(
            document_id=_clean(d.get("document_id")),
            cartridge_id=d.get("cartridge_id") or None,
            paper_title=_clean(d.get("paper_title")),
            central_question=_clean(d.get("central_question")),
            central_thesis=_clean(d.get("central_thesis")),
            paper_goal=_clean(d.get("paper_goal")),
            headline_claim=_clean(d.get("headline_claim")),
            claim_summaries=[
                ClaimSummary.from_dict(c)
                for c in (d.get("claim_summaries") or [])
                if _clean((c or {}).get("claim_id"))
            ],
            domains=[
                DomainOption.from_dict(dom)
                for dom in (d.get("domains") or [])
                if _clean((dom or {}).get("domain_key"))
            ],
            max_placements=(
                max_placements if max_placements > 0 else DEFAULT_MAX_PLACEMENTS
            ),
        )


@dataclass
class PlacementCandidate:
    """配置候補1件（常に inferred 相当。確定は教員のみ・LS3）。"""

    domain_key: str
    node_id: str
    perspective: str
    weight: float = UNPARSED_WEIGHT
    reason: str = ""
    evidence_quote: str = ""
    claim_id: str | None = None
    confidence: float = 0.0

    def key(self) -> tuple[str, str, str]:
        """一意キー（設計書 §5 の live 一意インデックスと同じ組）。"""
        return (self.domain_key, self.node_id, self.perspective)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "PlacementCandidate":
        d = d or {}
        claim_id = _clean(d.get("claim_id"))
        return cls(
            domain_key=_clean(d.get("domain_key")),
            node_id=_clean(d.get("node_id")),
            perspective=_clean(d.get("perspective")),
            weight=_weight_or_sentinel(d.get("weight")),
            reason=_clean(d.get("reason")),
            evidence_quote=_clean(d.get("evidence_quote")),
            claim_id=claim_id or None,
            confidence=_clamp(d.get("confidence")),
        )


@dataclass
class UnplacedDomain:
    """配置できなかったドメインの申告（LS10: 失敗ではなく信号）。"""

    domain_key: str
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "UnplacedDomain":
        d = d or {}
        return cls(
            domain_key=_clean(d.get("domain_key")),
            reason=_clean(d.get("reason")),
        )


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str  # "error" | "warning"
    message: str
    field: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LandscapePlacementResult:
    document_id: str
    placements: list[PlacementCandidate] = field(default_factory=list)
    unplaced_domains: list[UnplacedDomain] = field(default_factory=list)
    cartridge_id: str | None = None
    placement_version: str = LANDSCAPE_PLACEMENT_VERSION
    llm_call_count: int = 0
    truncated: bool = False
    truncated_count: int = 0
    skipped_reason: str | None = None
    review_notes: list[str] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "placements": [p.to_dict() for p in self.placements],
            "unplaced_domains": [u.to_dict() for u in self.unplaced_domains],
            "cartridge_id": self.cartridge_id,
            "placement_version": self.placement_version,
            "llm_call_count": self.llm_call_count,
            "truncated": self.truncated,
            "truncated_count": self.truncated_count,
            "skipped_reason": self.skipped_reason,
            "review_notes": list(self.review_notes),
            "validation_issues": [i.to_dict() for i in self.validation_issues],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict | None) -> "LandscapePlacementResult":
        d = d or {}
        return cls(
            document_id=_clean(d.get("document_id")),
            placements=[
                PlacementCandidate.from_dict(p) for p in (d.get("placements") or [])
            ],
            unplaced_domains=[
                UnplacedDomain.from_dict(u) for u in (d.get("unplaced_domains") or [])
            ],
            cartridge_id=d.get("cartridge_id") or None,
            placement_version=(
                _clean(d.get("placement_version")) or LANDSCAPE_PLACEMENT_VERSION
            ),
            llm_call_count=int(d.get("llm_call_count") or 0),
            truncated=bool(d.get("truncated")),
            truncated_count=int(d.get("truncated_count") or 0),
            skipped_reason=d.get("skipped_reason") or None,
            review_notes=list(d.get("review_notes") or []),
            validation_issues=[
                ValidationIssue(**i) for i in (d.get("validation_issues") or [])
            ],
        )

    @classmethod
    def make_skipped(
        cls,
        document_input: "LandscapePlacementInput",
        skipped_reason: str,
        note: str = "",
        *,
        cartridge_id: str | None = None,
        llm_call_count: int = 0,
        unplaced_domains: list[UnplacedDomain] | None = None,
        validation_issues: list[ValidationIssue] | None = None,
    ) -> "LandscapePlacementResult":
        """P4: 配置できなかったことを理由付きの結果として返す（黙って空にしない）。"""
        return cls(
            document_id=document_input.document_id,
            placements=[],
            unplaced_domains=list(unplaced_domains or []),
            cartridge_id=cartridge_id or document_input.cartridge_id,
            llm_call_count=llm_call_count,
            skipped_reason=skipped_reason,
            review_notes=[note] if note else [],
            validation_issues=list(validation_issues or []),
        )
