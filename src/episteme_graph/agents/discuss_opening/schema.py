"""DiscussOpeningAgent data models.

Design: ``docs/features/discuss_opening_authoring_design.md`` §4.1 (Phase 1,
migration 062). The agent generates the **one** opening ingredient that no
existing artifact contains: ``discussion_seed`` — questions that ask the
learner for a position, grounded in

- D-layer untested assumptions (``epistemic_ledger`` rows whose
  ``verification_status`` is ``untested`` / ``unknown``), and
- the author's *choices* — the ``operation`` column of derivation steps
  (``linearize_*`` / ``eliminate_*`` / ...), i.e. moves the author made that
  could have been made differently, and
- the paper's synthesized thesis text (``central_question`` /
  ``central_thesis.text``).

Everything else on the opening screen is a projection of already-generated
artifacts (design §3, Phase 0) and is NOT produced here.

Invariants carried by this module (design §1):

- OA2 candidate-only — this agent never marks anything approved. The
  orchestrator persists results as ``element_explanations`` candidates.
- OA5 information is never dropped — a document with no usable material is
  reported with ``skipped_reason`` set, never silently emptied.
- OA6 no raw numbers to the user — ``confidence`` lives in the DB
  ``evidence`` JSONB only; API layers convert to a coarse label.
- OA8 bounded teacher load — at most ``max_seeds`` seeds per document, and
  overflow is reported honestly via ``truncated`` / ``truncated_count``.

The agent never resolves opaque ids: the caller (``core/discuss/authoring.py``
via the orchestrator) hands over already-resolved *text* (design §4.1 —
"不透明 ID を渡さず解決済みテキストに展開する", the same rule as the two-layer
explanation agent's E4).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from episteme_graph.agents.cartridge_context import CartridgeContext

__all__ = [
    "CartridgeContext",
    "DISCUSS_OPENING_VERSION",
    "ROLE_DISCUSSION_SEED",
    "DEFAULT_LANGUAGE",
    "DEFAULT_MAX_SEEDS",
    "TARGET_MIN_SEEDS",
    "TARGET_MAX_SEEDS",
    "MAX_SEED_BODY_CHARS",
    "MAX_EVIDENCE_QUOTE_CHARS",
    "MIN_EVIDENCE_QUOTE_CHARS",
    "FORBIDDEN_PHRASES",
    "SKIPPED_REASON_NO_MATERIAL",
    "SKIPPED_REASON_LLM_CALL_FAILED",
    "SKIPPED_REASON_REPAIR_FAILED",
    "GROUNDING_ASSUMPTION",
    "GROUNDING_AUTHOR_CHOICE",
    "GROUNDING_THESIS",
    "ThesisFacts",
    "AssumptionFact",
    "AuthorChoice",
    "DiscussOpeningInput",
    "DiscussionSeed",
    "ValidationIssue",
    "DiscussOpeningResult",
]

DISCUSS_OPENING_VERSION = "v1"

# ``element_explanations.role``（migration 062）。コード側正本は
# ``core/element_explanations.py::ROLE_DISCUSSION_SEED`` だが、agent は backend/core を
# import しない（src 単体で動く）ため同じリテラルをここにも置く。値の一致は
# backend/tests/test_discuss_opening_authoring_guardrails.py が構造テストで固定する。
ROLE_DISCUSSION_SEED = "discussion_seed"

# 学習者向け散文なので日本語（設計書 §4.1「言語: `DISCUSS_OPENING_LANGUAGE`（既定 ja）
# 1つで決める。lecture_language は使えない」）。
DEFAULT_LANGUAGE = "ja"

# OA8: 1 document あたりの生成件数上限（既定 4 = `DISCUSS_OPENING_MAX_ITEMS_PER_DOCUMENT`）。
DEFAULT_MAX_SEEDS = 4
# 設計書 §4.1 が求める件数レンジ（プロンプトで狙う数。上限は max_seeds で切る）。
TARGET_MIN_SEEDS = 2
TARGET_MAX_SEEDS = 3

# 開幕画面に並ぶ短い問い。長文の解説にしない。
MAX_SEED_BODY_CHARS = 400
MAX_EVIDENCE_QUOTE_CHARS = 400
# あまりに短い「引用」は grounding の証明にならない（"the" 等）。
MIN_EVIDENCE_QUOTE_CHARS = 6

# D層と同じ禁止語彙（煽らない・断定しない。正本の並びは
# backend/tests/test_doubt_guardrails.py::BANNED と一致させる）。
FORBIDDEN_PHRASES = (
    "疑え",
    "ノーベル賞",
    "危険地帯",
    "要注意ゾーン",
    "崩壊させよ",
)

# skipped_reason 語彙（P4: 生成しなかったことを必ず理由付きで残す）。
SKIPPED_REASON_NO_MATERIAL = "no_source_material"
SKIPPED_REASON_LLM_CALL_FAILED = "llm_call_failed"
SKIPPED_REASON_REPAIR_FAILED = "repair_failed"

# seed が何を火種にしたか（監査・レビュー用の内訳。学習者向け表示には使わない）。
GROUNDING_ASSUMPTION = "untested_assumption"
GROUNDING_AUTHOR_CHOICE = "author_choice"
GROUNDING_THESIS = "thesis"
_GROUNDING_VOCAB = (GROUNDING_ASSUMPTION, GROUNDING_AUTHOR_CHOICE, GROUNDING_THESIS)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


@dataclass
class ThesisFacts:
    """thesis / skeleton artifact から解決済みの文（生 id は含まない）。"""

    central_question: str = ""
    paper_goal: str = ""
    central_thesis_text: str = ""

    def is_empty(self) -> bool:
        return not (self.central_question or self.paper_goal or self.central_thesis_text)

    def texts(self) -> list[str]:
        return [t for t in (self.central_question, self.paper_goal, self.central_thesis_text) if t]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ThesisFacts":
        d = d or {}
        return cls(
            central_question=_clean(d.get("central_question")),
            paper_goal=_clean(d.get("paper_goal")),
            central_thesis_text=_clean(d.get("central_thesis_text")),
        )


@dataclass
class AssumptionFact:
    """D層台帳の未検証行1件（statement は解決済みテキスト）。"""

    statement: str
    target_type: str = ""
    verification_status: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "AssumptionFact":
        d = d or {}
        return cls(
            statement=_clean(d.get("statement")),
            target_type=_clean(d.get("target_type")),
            verification_status=_clean(d.get("verification_status")),
        )


@dataclass
class AuthorChoice:
    """derivation step の operation 1件 = 著者が選んだ手（解決済みテキスト）。"""

    operation: str
    operation_subtype: str = ""
    reason: str = ""
    from_label: str = ""
    to_label: str = ""

    def texts(self) -> list[str]:
        return [
            t
            for t in (self.operation, self.operation_subtype, self.reason,
                      self.from_label, self.to_label)
            if t
        ]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "AuthorChoice":
        d = d or {}
        return cls(
            operation=_clean(d.get("operation")),
            operation_subtype=_clean(d.get("operation_subtype")),
            reason=_clean(d.get("reason")),
            from_label=_clean(d.get("from_label")),
            to_label=_clean(d.get("to_label")),
        )


@dataclass
class DiscussOpeningInput:
    """1 document 分の入力（LLM は 1 document = 1 コール・設計書 §4.1）。"""

    document_id: str
    thesis: ThesisFacts = field(default_factory=ThesisFacts)
    untested_assumptions: list[AssumptionFact] = field(default_factory=list)
    author_choices: list[AuthorChoice] = field(default_factory=list)
    language: str = DEFAULT_LANGUAGE
    max_seeds: int = DEFAULT_MAX_SEEDS

    def has_material(self) -> bool:
        """縮退判定（設計書 §4.1 / OA5）: 未検証前提も operation も無ければ生成しない。

        thesis だけがある document では**生成しない** — 根拠の無い火種を創作させない
        （thesis は Phase 0 の投影で既に画面に出ている）。
        """
        return bool(self.untested_assumptions or self.author_choices)

    def source_texts(self) -> list[str]:
        """evidence_quote の verbatim 検証に使う入力テキスト全部（順序は決定論）。"""
        texts: list[str] = list(self.thesis.texts())
        for assumption in self.untested_assumptions:
            if assumption.statement:
                texts.append(assumption.statement)
        for choice in self.author_choices:
            texts.extend(choice.texts())
        return texts

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "thesis": self.thesis.to_dict(),
            "untested_assumptions": [a.to_dict() for a in self.untested_assumptions],
            "author_choices": [c.to_dict() for c in self.author_choices],
            "language": self.language,
            "max_seeds": self.max_seeds,
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "DiscussOpeningInput":
        d = d or {}
        try:
            max_seeds = int(d.get("max_seeds") or DEFAULT_MAX_SEEDS)
        except (TypeError, ValueError):
            max_seeds = DEFAULT_MAX_SEEDS
        return cls(
            document_id=_clean(d.get("document_id")),
            thesis=ThesisFacts.from_dict(d.get("thesis")),
            untested_assumptions=[
                AssumptionFact.from_dict(a)
                for a in (d.get("untested_assumptions") or [])
                if _clean((a or {}).get("statement"))
            ],
            author_choices=[
                AuthorChoice.from_dict(c)
                for c in (d.get("author_choices") or [])
                if _clean((c or {}).get("operation"))
            ],
            language=_clean(d.get("language")) or DEFAULT_LANGUAGE,
            max_seeds=max_seeds if max_seeds > 0 else DEFAULT_MAX_SEEDS,
        )


@dataclass
class DiscussionSeed:
    """立場を求める問い1件（常に candidate。確定は教員のみ・OA2）。"""

    body: str
    evidence_quote: str = ""
    reason: str = ""
    confidence: float = 0.0
    grounded_in: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "DiscussionSeed":
        d = d or {}
        grounding = _clean(d.get("grounded_in"))
        return cls(
            body=_clean(d.get("body")),
            evidence_quote=_clean(d.get("evidence_quote")),
            reason=_clean(d.get("reason")),
            confidence=_clamp(d.get("confidence")),
            grounded_in=grounding if grounding in _GROUNDING_VOCAB else grounding,
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
class DiscussOpeningResult:
    document_id: str
    seeds: list[DiscussionSeed] = field(default_factory=list)
    language: str = DEFAULT_LANGUAGE
    cartridge_id: str | None = None
    opening_version: str = DISCUSS_OPENING_VERSION
    llm_call_count: int = 0
    truncated: bool = False
    truncated_count: int = 0
    skipped_reason: str | None = None
    review_notes: list[str] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "seeds": [s.to_dict() for s in self.seeds],
            "language": self.language,
            "cartridge_id": self.cartridge_id,
            "opening_version": self.opening_version,
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
    def from_dict(cls, d: dict | None) -> "DiscussOpeningResult":
        d = d or {}
        return cls(
            document_id=_clean(d.get("document_id")),
            seeds=[DiscussionSeed.from_dict(s) for s in (d.get("seeds") or [])],
            language=_clean(d.get("language")) or DEFAULT_LANGUAGE,
            cartridge_id=d.get("cartridge_id"),
            opening_version=_clean(d.get("opening_version")) or DISCUSS_OPENING_VERSION,
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
        document_input: "DiscussOpeningInput",
        skipped_reason: str,
        note: str = "",
        *,
        cartridge_id: str | None = None,
        llm_call_count: int = 0,
        validation_issues: list[ValidationIssue] | None = None,
    ) -> "DiscussOpeningResult":
        """P4: 生成できなかったことを理由付きの結果として返す（黙って空にしない）。"""
        return cls(
            document_id=document_input.document_id,
            seeds=[],
            language=document_input.language,
            cartridge_id=cartridge_id,
            llm_call_count=llm_call_count,
            skipped_reason=skipped_reason,
            review_notes=[note] if note else [],
            validation_issues=list(validation_issues or []),
        )
