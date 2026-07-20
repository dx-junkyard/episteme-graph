"""ContextualExplanationAgent data models.

Design: ``docs/features/hierarchical_context_explanation_design.md`` §5.1
(Phase 2, migration 056). Two layers of explanation are generated per
pipeline element (figure / theory_component / theory_claim / equation):

- ``contextual_explanation`` — this element's role IN THIS PAPER, woven from
  already-resolved upper (what it supports) / lower (what it is built from)
  context text. Always candidate-only; a teacher confirms it later via the
  ``element_explanations`` table (persistence is out of scope for this
  module — the orchestrator wiring is a later phase).
- ``generic_explanation`` — "what this element generally is". Produced ONLY
  when a confirmed L-layer (library) excerpt is supplied for the element
  (design principle E3 — hallucination guard: no library link -> no generic
  explanation, ever, regardless of what the model "knows").

The agent never resolves opaque ids itself: the caller (orchestrator, a
later phase) hands over ``ElementExplanationInput`` objects with all context
already resolved into text (design principle E4 — the structure stays
derived elsewhere; only the explanation *text* is generated/stored here).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from episteme_graph.agents.cartridge_context import CartridgeContext

__all__ = [
    "CartridgeContext",
    "CONTEXTUAL_EXPLANATION_VERSION",
    "ELEMENT_TYPES",
    "DEFAULT_MAX_ELEMENTS_PER_CALL",
    "MAX_CONTEXTUAL_EXPLANATION_CHARS",
    "MAX_GENERIC_EXPLANATION_CHARS",
    "SKIPPED_REASON_REPAIR_FAILED",
    "SKIPPED_REASON_LLM_CALL_FAILED",
    "SKIPPED_REASON_NO_INPUT",
    "ElementExplanationInput",
    "ElementExplanationResult",
    "ValidationIssue",
    "ContextualExplanationResult",
]

CONTEXTUAL_EXPLANATION_VERSION = "v1"

ELEMENT_TYPES = ("figure", "theory_component", "theory_claim", "equation")

DEFAULT_MAX_ELEMENTS_PER_CALL = 8

# Length caps keep explanations display-sized (design: contextual = 2-5
# sentences, generic = a short paraphrase of the library excerpt). The
# validator does not hard-enforce these (no character-count rule is listed
# in the design's validator section) but the prompt asks the model to stay
# within them.
MAX_CONTEXTUAL_EXPLANATION_CHARS = 1200
MAX_GENERIC_EXPLANATION_CHARS = 800

# Well-known ``skipped_reason`` values (P4: information is never silently
# dropped — an element that could not be explained is always kept in the
# result list, tagged with one of these reasons, never omitted).
SKIPPED_REASON_REPAIR_FAILED = "repair_failed"
SKIPPED_REASON_LLM_CALL_FAILED = "llm_call_failed"
SKIPPED_REASON_NO_INPUT = "no_input_context"


@dataclass
class ElementExplanationInput:
    """One element to explain, pre-resolved by the caller (no opaque ids).

    ``upper_context`` / ``lower_context`` entries have the shape
    ``{"relation": str, "text": str, "kind": str}`` (e.g. relation="supports",
    text="<thesis excerpt>", kind="thesis"). ``library_excerpt`` has the shape
    ``{"entry_id", "version_no", "name", "summary", "body_excerpt"}`` when a
    confirmed L-layer identity link exists for the element, else ``None``.
    """

    element_type: str
    element_id: str
    local_text: str
    upper_context: list[dict] = field(default_factory=list)
    lower_context: list[dict] = field(default_factory=list)
    library_excerpt: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ElementExplanationInput":
        return cls(
            element_type=str(d.get("element_type") or ""),
            element_id=str(d.get("element_id") or ""),
            local_text=str(d.get("local_text") or ""),
            upper_context=[dict(item) for item in (d.get("upper_context") or [])],
            lower_context=[dict(item) for item in (d.get("lower_context") or [])],
            library_excerpt=dict(d["library_excerpt"]) if d.get("library_excerpt") else None,
        )


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str  # "error" | "warning"
    message: str
    field: str | None = None


@dataclass
class ElementExplanationResult:
    element_id: str
    element_type: str
    contextual_explanation: str | None = None
    generic_explanation: str | None = None
    evidence_quote: str = ""
    reason: str = ""
    confidence: float = 0.0
    skipped_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ElementExplanationResult":
        return cls(
            element_id=str(d.get("element_id") or ""),
            element_type=str(d.get("element_type") or ""),
            contextual_explanation=d.get("contextual_explanation"),
            generic_explanation=d.get("generic_explanation"),
            evidence_quote=str(d.get("evidence_quote") or ""),
            reason=str(d.get("reason") or ""),
            confidence=float(d.get("confidence") or 0.0),
            skipped_reason=d.get("skipped_reason"),
        )

    @classmethod
    def make_skipped(
        cls,
        element_input: "ElementExplanationInput",
        reason: str,
        skipped_reason: str = SKIPPED_REASON_REPAIR_FAILED,
    ) -> "ElementExplanationResult":
        """P4: an element that cannot be explained is still returned, never dropped."""
        return cls(
            element_id=element_input.element_id,
            element_type=element_input.element_type,
            contextual_explanation=None,
            generic_explanation=None,
            evidence_quote="",
            reason=reason,
            confidence=0.0,
            skipped_reason=skipped_reason,
        )


@dataclass
class ContextualExplanationResult:
    elements: list[ElementExplanationResult]
    cartridge_id: str | None = None
    explanation_version: str = CONTEXTUAL_EXPLANATION_VERSION
    llm_call_count: int = 0
    truncated: bool = False
    review_notes: list[str] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "elements": [e.to_dict() for e in self.elements],
            "cartridge_id": self.cartridge_id,
            "explanation_version": self.explanation_version,
            "llm_call_count": self.llm_call_count,
            "truncated": self.truncated,
            "review_notes": list(self.review_notes),
            "validation_issues": [asdict(i) for i in self.validation_issues],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "ContextualExplanationResult":
        return cls(
            elements=[ElementExplanationResult.from_dict(e) for e in d.get("elements", [])],
            cartridge_id=d.get("cartridge_id"),
            explanation_version=d.get("explanation_version", CONTEXTUAL_EXPLANATION_VERSION),
            llm_call_count=int(d.get("llm_call_count") or 0),
            truncated=bool(d.get("truncated", False)),
            review_notes=list(d.get("review_notes") or []),
            validation_issues=[ValidationIssue(**i) for i in d.get("validation_issues", [])],
        )

    @classmethod
    def make_fallback(
        cls,
        elements_in: list[ElementExplanationInput],
        cartridge_id: str | None,
        reason: str,
    ) -> "ContextualExplanationResult":
        return cls(
            elements=[
                ElementExplanationResult.make_skipped(e, reason, SKIPPED_REASON_LLM_CALL_FAILED)
                for e in elements_in
            ],
            cartridge_id=cartridge_id,
            review_notes=[reason],
        )
