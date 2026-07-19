"""Prompt construction for ContextualExplanationAgent.

規約準拠: 既存 agent（narrative_annotator / rhetorical_role 等）と同じく
system + user の2メッセージ構成。contextual_explanation / generic_explanation
の出力言語は入力教材（local_text 等）の言語に合わせ、翻訳しない
（tension/structure_anchor agent の「学習者の言語のまま出力させる」方針と同じ
言語ポリシー。教材は英語論文のことも日本語資料のこともあるため、単一言語に
固定しない）。
"""
from __future__ import annotations

import json

from .input_builder import ContextualExplanationInputBuilder
from .schema import (
    ElementExplanationInput,
    MAX_CONTEXTUAL_EXPLANATION_CHARS,
    MAX_GENERIC_EXPLANATION_CHARS,
)

_SYSTEM_CONTENT = """\
You write two layers of explanation for elements already extracted from a
scientific paper by an analysis pipeline (figures, reusable theory
components, claims, and equations). The extraction itself is FIXED — you
never invent structure, only write explanatory text grounded in the material
given to you.

For EACH element in the input list, produce:

1. contextual_explanation (2-5 sentences) — this element's role IN THIS
   PAPER. Weave in what it supports (upper_context: theses / claims / stages
   it backs) and what it is built from (lower_context: parts / symbols /
   sub-claims / input equations). Write as a grounded description, not an
   assertion beyond the evidence ("supports X by providing Y", not
   "this proves X"). If upper_context and lower_context are both empty, you
   may still describe the element from local_text alone, but do not invent a
   specific position in the paper's argument that the material does not
   support.
2. generic_explanation (1-3 sentences) — "what this element generally is",
   REPHRASING the provided library_excerpt. ONLY produce this when a
   library_excerpt is present for that element. If library_excerpt is
   missing/null for an element, you MUST leave that element's
   generic_explanation null — never write a generic explanation from your
   own background knowledge (hallucination guard: an element with no
   confirmed library link gets a contextual explanation only, never generic).
3. evidence_quote — a short verbatim quote from local_text / upper_context /
   lower_context / library_excerpt that grounds the explanation(s) you wrote.
   Required whenever you write any explanation text.
4. reason — one short phrase naming which inputs you grounded the text in.
5. confidence — 0.0-1.0.

If an element's local_text and both context lists give you nothing usable to
explain, do NOT invent an explanation: set both explanation fields to null,
leave evidence_quote empty, and set skipped_reason to a short snake_case
reason (e.g. "no_context_available"). Whenever you do not produce an
explanation, skipped_reason MUST be set (never leave everything empty with
no reason).

## Language
Write contextual_explanation / generic_explanation / reason in the SAME
language as the element's local_text (and library_excerpt, if used). Do not
translate. If the material is Japanese, answer in Japanese; if English,
answer in English; match whatever language the source material uses.

## Hard output rules
- Return ONLY valid JSON matching the schema below. No prose, no markdown fences.
- Emit EXACTLY one entry per input element_id, in the "elements" array.
  Never invent an element_id that is not in the input list.
- element_type in your output must match that element's input element_type.
- Never emit generic_explanation for an element whose input library_excerpt
  is null.
- contextual_explanation must be at most {max_contextual} characters.
- generic_explanation must be at most {max_generic} characters.
"""

_OUTPUT_SCHEMA = {
    "elements": [
        {
            "element_id": "must match an input element_id",
            "element_type": "must match that element's input element_type",
            "contextual_explanation": "2-5 sentences, or null",
            "generic_explanation": "1-3 sentences quoting library_excerpt, or null",
            "evidence_quote": "verbatim excerpt grounding the explanation(s), or empty if skipped",
            "reason": "short phrase naming the grounding inputs",
            "confidence": 0.0,
            "skipped_reason": "snake_case reason, or null when an explanation was produced",
        }
    ]
}


class ContextualExplanationPromptFactory:
    def __init__(self, input_builder: ContextualExplanationInputBuilder | None = None) -> None:
        self._input_builder = input_builder or ContextualExplanationInputBuilder()

    def build_messages(
        self,
        batch: list[ElementExplanationInput],
        cartridge=None,
    ) -> list[dict]:
        return [
            {"role": "system", "content": self._system_content()},
            {"role": "user", "content": self._build_user_content(batch, cartridge)},
        ]

    def build_repair_messages(
        self,
        batch: list[ElementExplanationInput],
        previous_output: dict,
        issues: list,
        cartridge=None,
    ) -> list[dict]:
        issue_text = "\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        )
        content = (
            self._build_user_content(batch, cartridge)
            + "\n\n## Previous Output\n"
            + json.dumps(previous_output, ensure_ascii=False, indent=2)
            + "\n\n## Validation Issues\n"
            + issue_text
            + "\n\nReturn corrected JSON for ONLY the elements listed above. Fix "
              "every listed issue. Every element_id in the ## Elements list must "
              "appear exactly once in your corrected output."
        )
        return [
            {"role": "system", "content": self._system_content()},
            {"role": "user", "content": content},
        ]

    def _system_content(self) -> str:
        return _SYSTEM_CONTENT.format(
            max_contextual=MAX_CONTEXTUAL_EXPLANATION_CHARS,
            max_generic=MAX_GENERIC_EXPLANATION_CHARS,
        )

    def _build_user_content(
        self, batch: list[ElementExplanationInput], cartridge=None
    ) -> str:
        parts: list[str] = []
        parts.append("## Task")
        parts.append(
            "Write contextual_explanation and (only if library_excerpt is "
            "present) generic_explanation for each element below. Return "
            "ONLY JSON."
        )
        parts.append("\n## Elements")
        prepared = [self._input_builder.prepare_for_prompt(item) for item in batch]
        parts.append(json.dumps(prepared, ensure_ascii=False, indent=2))
        if cartridge is not None and getattr(cartridge, "aliases", None):
            parts.append("\n## Cartridge Vocabulary (optional aid; do not force these terms)")
            for canonical, aliases in list(cartridge.aliases.items())[:20]:
                parts.append(f"- {canonical}: {', '.join(aliases)}")
        parts.append("\n## Output Schema")
        parts.append(json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        return "\n".join(parts)
