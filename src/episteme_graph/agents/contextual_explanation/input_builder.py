"""Batch preparation for ContextualExplanationAgent.

Design: ``docs/features/hierarchical_context_explanation_design.md`` §5.1.

The caller (orchestrator, a later phase) hands over already-resolved
``ElementExplanationInput`` objects — this agent never resolves ids itself
(design principle E4: upper/lower structure stays derived elsewhere, only
the explanation text is generated here). This module's job is therefore
narrower than a typical agent input_builder:

1. Group elements into LLM-call-sized batches (``max_elements_per_call``,
   design §5.1 — "1 LLM コールに複数要素をバッチする").
2. Defensively bound each element's text so a single batch cannot blow the
   prompt budget (a caller could hand over an unbounded number of context
   items or an oversized library excerpt).
3. Strip internal identifiers (``tex_b80`` / ``eq_tex_b78`` …) from the prose
   material (IK-0819). Upstream generated text — e.g. an equation_semantics
   summary used as ``local_text`` — can carry block ids the LLM copied from its
   own material; showing them here would invite the explanation to repeat them.
   ``element_id`` itself is kept (it is the address the output is matched by).
"""
from __future__ import annotations

from ..internal_id_text import strip_internal_ids
from .schema import DEFAULT_MAX_ELEMENTS_PER_CALL, ElementExplanationInput

# Defensive prompt-size bounds. These are intentionally generous — the goal
# is to guard against pathological inputs, not to compress normal ones.
MAX_CONTEXT_ITEMS_PER_ELEMENT = 12
MAX_CONTEXT_ITEM_CHARS = 600
MAX_LOCAL_TEXT_CHARS = 2000
MAX_LIBRARY_EXCERPT_CHARS = 800

_TRUNCATION_MARK = "…"  # "…"


class ContextualExplanationInputBuilder:
    """Batches elements for LLM calls and bounds their text for the prompt."""

    def build_batches(
        self,
        elements: list[ElementExplanationInput],
        max_elements_per_call: int | None = None,
    ) -> list[list[ElementExplanationInput]]:
        size = int(max_elements_per_call or DEFAULT_MAX_ELEMENTS_PER_CALL)
        if size < 1:
            size = DEFAULT_MAX_ELEMENTS_PER_CALL
        # Skip elements without an id: they cannot be referenced in output,
        # matched during validation, or persisted downstream.
        cleaned = [e for e in (elements or []) if str(e.element_id or "").strip()]
        return [cleaned[i : i + size] for i in range(0, len(cleaned), size)]

    def prepare_for_prompt(self, item: ElementExplanationInput) -> dict:
        """Return a bounded dict representation of ``item`` for the prompt.

        Never mutates ``item`` itself — only the rendered copy is truncated.
        """
        return {
            "element_id": item.element_id,
            "element_type": item.element_type,
            "local_text": _prose(item.local_text, MAX_LOCAL_TEXT_CHARS),
            "upper_context": self._bounded_context(item.upper_context),
            "lower_context": self._bounded_context(item.lower_context),
            "library_excerpt": self._bounded_excerpt(item.library_excerpt),
        }

    @staticmethod
    def _bounded_context(items: list[dict] | None) -> list[dict]:
        bounded: list[dict] = []
        for entry in (items or [])[:MAX_CONTEXT_ITEMS_PER_ELEMENT]:
            if not isinstance(entry, dict):
                continue
            bounded.append({
                "relation": str(entry.get("relation") or ""),
                "kind": str(entry.get("kind") or ""),
                "text": _prose(str(entry.get("text") or ""), MAX_CONTEXT_ITEM_CHARS),
            })
        return bounded

    @staticmethod
    def _bounded_excerpt(excerpt: dict | None) -> dict | None:
        if not excerpt:
            return None
        return {
            "entry_id": str(excerpt.get("entry_id") or ""),
            "version_no": excerpt.get("version_no"),
            "name": str(excerpt.get("name") or ""),
            "summary": _prose(str(excerpt.get("summary") or ""), MAX_LIBRARY_EXCERPT_CHARS),
            "body_excerpt": _prose(
                str(excerpt.get("body_excerpt") or ""), MAX_LIBRARY_EXCERPT_CHARS
            ),
        }


def _prose(text: str, limit: int) -> str:
    """Prose material for the prompt: internal ids stripped, then bounded."""
    return _truncate(strip_internal_ids(text), limit)


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + _TRUNCATION_MARK
