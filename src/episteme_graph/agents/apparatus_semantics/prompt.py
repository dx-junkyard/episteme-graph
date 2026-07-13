"""Prompt construction for ApparatusSemanticsAgent.

Domain-independent by design (principle #5): apparatus vocabulary is never
hardcoded here. Candidate briefs (from the frozen library, §5-3) and cartridge
hints are supplied as few-shot knowledge only; the model is always allowed to
answer novel/unknown when nothing matches.
"""
from __future__ import annotations

import json

from .schema import MATCH_STATUSES, FigureImageInput

_SYSTEM_CONTENT = """\
You are analyzing a figure image from a scientific paper (an experimental \
apparatus / instrument / setup diagram).

Your task is NOT to judge whether the apparatus works or whether the paper's \
claims are correct. Your task is to describe what is depicted:

1. A short candidate name for the depicted apparatus/instrument
2. Whether it matches one of the supplied library candidates, is a novel \
   apparatus not in the library, or cannot be determined (unknown)
3. The apparatus's constituent parts and their role
4. How the parts connect to each other
5. Evidence: quote the caption/nearby text verbatim wherever it supports your \
   reading. Do not invent quotes.

Be conservative. If the image or caption does not give enough signal, use \
match_status="unknown" and a low confidence. Never claim a match to a \
candidate unless the image clearly supports it.

Return ONLY valid JSON matching the output schema — no markdown fences.
"""

_OUTPUT_SCHEMA = {
    "apparatus_name_candidate": "short free-text name for the depicted apparatus",
    "matched_library_entry_id": "entry_id of a candidate below, or null",
    "match_status": "matched | novel | unknown",
    "parts": [
        {
            "name": "string",
            "role": "string — this part's function in the apparatus",
            "evidence_quote": "verbatim quote from caption/nearby text, or empty string",
            "reason": "string",
            "confidence": 0.0,
        }
    ],
    "connections": [
        {
            "from_part": "a part name from 'parts' above",
            "to_part": "a part name from 'parts' above",
            "relation": "string — how from_part relates to/connects with to_part",
            "reason": "string",
            "confidence": 0.0,
        }
    ],
    "evidence_quote": "verbatim quote from caption/nearby text supporting the overall identification, or empty string",
    "reason": "string — overall justification",
    "confidence": 0.0,
}


class ApparatusSemanticsPromptFactory:
    def build_messages(
        self,
        figure: FigureImageInput,
        candidate_briefs: list[dict],
        nearby_text: list[str],
        cartridge_hints: dict,
    ) -> list[dict]:
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": self._build_user_content(
                figure, candidate_briefs, nearby_text, cartridge_hints,
            )},
        ]

    def build_repair_messages(
        self,
        figure: FigureImageInput,
        candidate_briefs: list[dict],
        nearby_text: list[str],
        cartridge_hints: dict,
        previous_output: dict,
        issues: list,
    ) -> list[dict]:
        issue_text = "\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        )
        content = (
            self._build_user_content(figure, candidate_briefs, nearby_text, cartridge_hints)
            + "\n\n## Previous Output\n"
            + json.dumps(previous_output, ensure_ascii=False, indent=2)
            + "\n\n## Validation Issues\n"
            + issue_text
            + "\nReturn corrected JSON for this same figure."
        )
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": content},
        ]

    def _build_user_content(
        self,
        figure: FigureImageInput,
        candidate_briefs: list[dict],
        nearby_text: list[str],
        cartridge_hints: dict,
    ) -> str:
        parts: list[str] = []
        parts.append("## Task")
        parts.append(
            "Identify the apparatus shown in the attached image using the caption, "
            "nearby text, and (if any) the library candidates below. "
            "Return ONLY JSON matching the output schema."
        )

        parts.append("\n## Figure Context")
        parts.append(f"figure_id: {figure.figure_id}")
        parts.append(f"figure_key: {figure.figure_key}")
        if figure.figure_label:
            parts.append(f"figure_label: {figure.figure_label}")
        parts.append(f"caption: {figure.caption_text}")
        if figure.figure_record:
            parts.append("figure_record (from FigureTableSemanticsAgent):")
            parts.append(json.dumps(figure.figure_record, ensure_ascii=False, indent=2))

        if nearby_text:
            parts.append("\n## Nearby Text")
            parts.extend(f"- {t}" for t in nearby_text)

        if candidate_briefs:
            parts.append(
                "\n## Library Candidates (retrieved from the frozen knowledge "
                "library — choose one only if the image clearly matches; "
                "otherwise use match_status='novel' or 'unknown')"
            )
            parts.append(json.dumps(candidate_briefs, ensure_ascii=False, indent=2))
        else:
            parts.append(
                "\n## Library Candidates\n"
                "None retrieved. This may be the first time this apparatus type "
                "is analyzed, or the library is empty for this domain — use "
                "match_status='novel' (or 'unknown' if the apparatus cannot be "
                "identified from the image/caption)."
            )

        if cartridge_hints.get("component_types"):
            parts.append("\n## Cartridge Component-Type Vocabulary (hint only, not exhaustive)")
            parts.append(json.dumps(cartridge_hints["component_types"][:30], ensure_ascii=False))
        if cartridge_hints.get("aliases"):
            parts.append("\n## Cartridge Term Aliases (hint only)")
            parts.append(json.dumps(cartridge_hints["aliases"], ensure_ascii=False))

        parts.append("\n## Output Schema")
        parts.append(json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        parts.append("\n## Allowed Values")
        parts.append(f"match_status: {', '.join(MATCH_STATUSES)}")
        parts.append("\n## Constraints")
        parts.append(
            "- matched_library_entry_id must be one of the candidates' entry_id values above, "
            "or null when match_status is not 'matched'\n"
            "- evidence_quote fields must be verbatim substrings of the caption or nearby text "
            "(or an empty string if nothing supports it — never invent a quote)\n"
            "- from_part / to_part in connections must reference a name from parts[]\n"
            "- confidence must be between 0.0 and 1.0\n"
            "- Return ONLY valid JSON, no markdown fences"
        )
        return "\n".join(parts)
