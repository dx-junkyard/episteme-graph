"""Prompt construction for ClaimQualificationAgent."""
from __future__ import annotations

import json

from .schema import (
    ATOMIC_CLAIM_ATOMICITY,
    ATOMIC_CLAIM_STATUSES,
    CLAIM_TIERS,
    CORE_CLAIM_TYPE_FALLBACKS,
    EVIDENCE_ADEQUACY,
    GRANULARITIES,
    QUALIFICATION_STATUSES,
    REVIEWABILITY,
    CartridgeContext,
    QualificationLLMInput,
)

_SYSTEM_CONTENT = """\
You are evaluating whether a span from a scientific paper should become a reusable claim,
and—when the span mixes several statements—rewriting it into atomic claims.

Your task is NOT to summarize the paper.
Your task is to (a) judge whether the span is a reusable minimal claim and
(b) perform atomic rewrite: turn a long / paper-level / multi-proposition span into
a list of atomic claims, each a single minimal proposition.

A good (atomic) claim should be:
1. content-bearing
2. one minimal proposition (1 claim = 1 fact / relation / step)
3. a standalone sentence — full subject, no unresolved pronoun/anaphora
4. reviewable against the source text
5. reusable in downstream components as input/output/precondition/caution

Rules:
- role_labels are context, not final adoption decisions.
- Figure narration, section meta, and empty discourse should usually not become claims.
- Prior-work statements may be preserved, but must not be paper_core.
- If uncertain, prefer deferred over an overconfident rejected/accepted decision.
- Prefer cartridge-normalized terminology when available.

Atomic rewrite (the formal responsibility of this step):
- If the span contains multiple propositions (long sentence, several sentences,
  coordinated clauses, or a paper-level summary), set should_split=true and emit
  one entry per minimal proposition in `atomic_claims`.
- Each atomic claim MUST be a complete standalone sentence:
  * supply the omitted subject (do NOT start with "Shows ...", "And yields ...").
  * resolve pronouns / "this result" into the concrete referent.
  * never begin with a connector (and / but / therefore / then / thus / hence / moreover ...).
- Preserve the full meaning of the original span across the atomic claims; drop nothing.
- For each atomic claim set atomicity="atomic" and status="accepted".
- If you CANNOT confidently atomize part of the span, keep that piece as an
  atomic_claims entry with atomicity="non_atomic", status="review_required" and a
  qualification_reason such as "contains multiple propositions; split required".
- If the span is already a single minimal proposition, leave `atomic_claims` empty
  and set should_split=false.
- Each atomic claim must stay traceable: set source_span_id to the input span_id and
  copy the supporting source phrase into evidence_quote.
- Return ONLY valid JSON matching the provided schema.
"""

_OUTPUT_SCHEMA = {
    "span_id": "string",
    "block_id": "string",
    "section_id": "string or null",
    "text": "string",
    "role_labels": ["role labels copied from input"],
    "qualification": {
        "status": "accepted | rejected | deferred",
        "claim_tier": "paper_core | paper_supporting | background | prior_work | meta",
        "claim_type_candidate": "core fallback type or cartridge-defined type",
        "granularity": "good | too_broad | too_narrow",
        "evidence_adequacy": "sufficient | weak | broken",
        "reviewability": "good | moderate | poor",
    },
    "edit_suggestions": {
        "should_split": False,
        "should_merge_with_prev": False,
        "should_merge_with_next": False,
        "normalized_text_hint": "string or empty",
    },
    "atomic_claims": [
        {
            "text": "a single standalone minimal proposition",
            "normalized_text": "normalized phrasing (cartridge terms when available)",
            "claim_type_candidate": "core fallback type or cartridge-defined type",
            "atomicity": "atomic | non_atomic",
            "status": "accepted | review_required",
            "source_span_id": "span_id this claim came from",
            "evidence_quote": "supporting phrase copied from the source span",
            "qualification_reason": "why this is (or is not) a confirmed atomic claim",
            "confidence": 0.0,
        }
    ],
    "reason": "short reason grounded in the span and context",
    "confidence": 0.0,
}


class ClaimQualificationPromptFactory:
    def build_messages(
        self,
        llm_input: QualificationLLMInput,
        cartridge: CartridgeContext | None = None,
    ) -> list[dict]:
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": self._build_user_content(llm_input, cartridge)},
        ]

    def build_repair_messages(
        self,
        llm_input: QualificationLLMInput,
        previous_output: dict,
        issues: list,
        cartridge: CartridgeContext | None = None,
    ) -> list[dict]:
        issue_text = "\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        )
        content = (
            self._build_user_content(llm_input, cartridge)
            + "\n\n## Previous Output\n"
            + json.dumps(previous_output, ensure_ascii=False, indent=2)
            + "\n\n## Validation Issues\n"
            + issue_text
            + "\nReturn corrected JSON for this same span."
        )
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": content},
        ]

    def build_reference_repair_messages(
        self,
        llm_input: QualificationLLMInput,
        previous_output: dict,
        unresolved_fragments: list[str],
        cartridge: CartridgeContext | None = None,
    ) -> list[dict]:
        """Single targeted retry for unresolved anaphora (issue #357)."""
        fragments = "\n".join(f"- {f!r}" for f in unresolved_fragments)
        content = (
            self._build_user_content(llm_input, cartridge)
            + "\n\n## Previous Output\n"
            + json.dumps(previous_output, ensure_ascii=False, indent=2)
            + "\n\n## Unresolved References\n"
            + "The following atomic claims still begin with an unresolved "
            + "pronoun / demonstrative and cannot stand alone:\n"
            + fragments
            + "\n\nRewrite each flagged atomic claim so its subject names the "
            + "concrete referent from the span or source block (e.g. replace "
            + "\"This result shows\" with \"<the actual quantity/relation> shows\"). "
            + "Keep every other field unchanged. Return corrected JSON for this same span."
        )
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": content},
        ]

    def _build_user_content(
        self,
        llm_input: QualificationLLMInput,
        cartridge: CartridgeContext | None,
    ) -> str:
        parts: list[str] = []

        parts.append("## Task")
        parts.append(
            "Decide whether the span should be accepted, rejected, or deferred as a reusable minimal claim.\n"
            "Return ONLY JSON matching the output schema."
        )

        parts.append("\n## Span Context")
        parts.append(f"document_id: {llm_input.document_id}")
        parts.append(f"span_id: {llm_input.span_id}")
        parts.append(f"block_id: {llm_input.block_id}")
        parts.append(f"section_id: {llm_input.section_id}")
        if llm_input.section_title:
            parts.append(f"section_title: {llm_input.section_title}")
        if llm_input.backbone_block_type:
            parts.append(f"backbone_block_type: {llm_input.backbone_block_type}")
        if llm_input.headline_claim:
            parts.append(f"headline_claim: {llm_input.headline_claim}")
        parts.append(f"role_labels: {', '.join(llm_input.role_labels)}")
        parts.append(f"is_claim_candidate: {llm_input.is_claim_candidate}")
        parts.append(f"is_reject_candidate: {llm_input.is_reject_candidate}")

        if llm_input.prev_span_text:
            parts.append("\n## Previous Span")
            parts.append(llm_input.prev_span_text)
        parts.append("\n## Current Span")
        parts.append(llm_input.span_text)
        if llm_input.next_span_text:
            parts.append("\n## Next Span")
            parts.append(llm_input.next_span_text)
        if llm_input.source_block_text:
            parts.append("\n## Source Block")
            parts.append(llm_input.source_block_text)

        if llm_input.normalized_terms:
            parts.append("\n## Cartridge Normalized Terms")
            for term in llm_input.normalized_terms[:20]:
                aliases = term.get("aliases", [])
                line = f"- {term.get('canonical', '')}"
                if aliases:
                    line += f" (aliases: {', '.join(str(a) for a in aliases[:5])})"
                if term.get("hints"):
                    line += f" hints: {term.get('hints')}"
                parts.append(line)

        if cartridge and cartridge.notation_patterns:
            parts.append("\n## Cartridge Notation Patterns")
            parts.append(json.dumps(cartridge.notation_patterns[:20], ensure_ascii=False))

        parts.append("\n## Output Schema")
        parts.append(json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2))

        parts.append("\n## Allowed Values")
        parts.append(f"status: {', '.join(QUALIFICATION_STATUSES)}")
        parts.append(f"claim_tier: {', '.join(CLAIM_TIERS)}")
        parts.append(f"granularity: {', '.join(GRANULARITIES)}")
        parts.append(f"evidence_adequacy: {', '.join(EVIDENCE_ADEQUACY)}")
        parts.append(f"reviewability: {', '.join(REVIEWABILITY)}")
        parts.append(
            "claim_type_candidate core fallbacks: "
            + ", ".join(CORE_CLAIM_TYPE_FALLBACKS)
        )
        parts.append(f"atomic_claims[].atomicity: {', '.join(ATOMIC_CLAIM_ATOMICITY)}")
        parts.append(f"atomic_claims[].status: {', '.join(ATOMIC_CLAIM_STATUSES)}")

        parts.append("\n## Constraints")
        parts.append(
            "- accepted spans should be content-bearing and reviewable\n"
            "- prior_work may be accepted as prior_work tier, but must not be paper_core\n"
            "- figure_narration / section_meta / meta_discourse should usually be rejected as meta\n"
            "- too_broad / multi-proposition spans MUST set should_split=true and fill atomic_claims\n"
            "- every atomic claim must be a standalone sentence (full subject, no leading connector)\n"
            "- atomic claims together must preserve the full meaning of the span (drop nothing)\n"
            "- if a piece cannot be atomized set its atomicity=non_atomic, status=review_required\n"
            "- too_narrow spans should normally be deferred/rejected or suggest merge\n"
            "- confidence must be between 0.0 and 1.0\n"
            "- Return ONLY valid JSON, no markdown fences"
        )

        return "\n".join(parts)
