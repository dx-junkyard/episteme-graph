"""Prompt construction for ApparatusSemanticsAgent.

Domain-independent by design (principle #5): apparatus vocabulary is never
hardcoded here. Candidate briefs (from the frozen library, §5-3) and cartridge
hints are supplied as few-shot knowledge only; the model is always allowed to
answer novel/unknown when nothing matches.
"""
from __future__ import annotations

import json

from .schema import MATCH_STATUSES, FigureImageInput
from episteme_graph.agents.figure_modes import FIGURE_MODES

_SYSTEM_CONTENT = """\
You are analyzing a figure image from a scientific paper. It may be a \
functional diagram, a data plot, a descriptive photo/illustration, a mixed \
multi-panel figure, or something that cannot be classified. Do not assume \
that every image is an apparatus diagram.

Your task is NOT to judge whether the apparatus works or whether the paper's \
claims are correct. Your task is to describe what is depicted:

1. Suggest one presentation mode and explain the classification
2. Produce the matching mode-specific analysis profile
3. For functional diagrams only, identify the functions, their multiple \
   inputs/outputs, and how outputs connect to inputs
4. Preserve the legacy apparatus fields for functional diagrams; leave them \
   empty for data plots and descriptive images
5. Evidence: quote the caption/nearby text verbatim wherever it supports your \
   reading. Do not invent quotes.

Be conservative. If the image or caption does not give enough signal, use \
match_status="unknown" and a low confidence. Never claim a match to a \
candidate unless the image clearly supports it.

Return ONLY valid JSON matching the output schema — no markdown fences.
"""

_OUTPUT_SCHEMA = {
    "suggested_mode": "functional_diagram | data_plot | descriptive_image | mixed | unknown",
    "mode_reason": "short reason; distinguish direct evidence from visual inference",
    "analysis_profile": {
        "overall_function": "functional_diagram: purpose of the whole system",
        "external_inputs": [{"id": "input id", "name": "input", "type": "light/signal/material/information/etc"}],
        "external_outputs": [{"id": "output id", "name": "output", "type": "light/signal/material/information/etc"}],
        "functions": [{
            "id": "stable local id",
            "name": "display name",
            "role": "function performed",
            "inputs": [{"id": "input id", "name": "port/input", "type": "what is received"}],
            "outputs": [{"id": "output id", "name": "port/output", "type": "what is emitted"}],
            "label_ref": "in-figure label verbatim, or null",
        }],
        "connections": [{
            "from_function_id": "function id",
            "from_output_id": "output id",
            "to_function_id": "function id",
            "to_input_id": "input id",
            "relation": "how/what is transferred",
        }],
        "plot_type": "data_plot: line/bar/scatter/heatmap/etc",
        "axes": [{"orientation": "x/y/color/other", "name": "axis label", "unit": "unit", "scale": "linear/log/unknown"}],
        "series": [{"name": "series", "visual_encoding": "color/line/marker"}],
        "observations": [{"id": "stable observation id", "kind": "peak/trend/crossing/etc", "description": "direct observation"}],
        "interpretations": [{"observation_id": "optional observation id", "meaning_candidate": "possible meaning, kept separate from observation"}],
        "highlights": [{"label": "point/region", "description": "why it matters"}],
        "summary": "descriptive_image/mixed/unknown: concise explanation",
        "subjects": [{"name": "subject", "description": "what is visibly depicted"}],
        "regions": [{"label": "region", "bbox": [0, 0, 0, 0], "description": "region explanation"}],
        "teaching_points": [{"title": "point", "description": "instructional explanation"}],
        "panels": [{"label": "(a)", "mode": "one allowed mode", "analysis": {}}],
    },
    "apparatus_name_candidate": "short free-text name for the depicted apparatus",
    "matched_library_entry_id": "entry_id of a candidate below, or null",
    "match_status": "matched | novel | unknown",
    "parts": [
        {
            "name": "string",
            "role": "string — this part's function in the apparatus",
            "label_ref": "one of the in-figure labels verbatim, or null",
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

# Suggested (not enforced — validator.py never hard-constrains this vocabulary)
# relation vocabulary for ``connections[].relation``, used only when the
# cartridge does not supply its own ``relation_types`` extraction hint.
_DEFAULT_CONNECTION_RELATIONS = (
    "optical_path",
    "electrical_signal",
    "feedback_control",
    "mechanical",
)


class ApparatusSemanticsPromptFactory:
    def build_messages(
        self,
        figure: FigureImageInput,
        candidate_briefs: list[dict],
        nearby_text: list[str],
        cartridge_hints: dict,
        inner_label_hints: list[str] | None = None,
        abbreviations: dict | None = None,
    ) -> list[dict]:
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": self._build_user_content(
                figure, candidate_briefs, nearby_text, cartridge_hints,
                inner_label_hints, abbreviations,
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
        inner_label_hints: list[str] | None = None,
        abbreviations: dict | None = None,
    ) -> list[dict]:
        issue_text = "\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        )
        content = (
            self._build_user_content(
                figure, candidate_briefs, nearby_text, cartridge_hints,
                inner_label_hints, abbreviations,
            )
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
        inner_label_hints: list[str] | None = None,
        abbreviations: dict | None = None,
    ) -> str:
        parts: list[str] = []
        parts.append("## Task")
        parts.append(
            "Classify and analyze the attached scientific figure using the caption, "
            "nearby text, and (for functional apparatus diagrams only) any library candidates below. "
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

        if inner_label_hints:
            parts.append(
                "\n## In-Figure Labels (extracted deterministically from the PDF text layer)"
            )
            parts.extend(f"- {label}" for label in inner_label_hints)

        if abbreviations:
            parts.append("\n## Abbreviation Dictionary (from the paper body)")
            parts.append(json.dumps(abbreviations, ensure_ascii=False, indent=2))
            parts.append(
                "You may use an abbreviation's expansion when choosing a part's "
                "name, but label_ref must stay the in-figure label string "
                "verbatim (do not expand it)."
            )

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

        relation_types = cartridge_hints.get("relation_types") or list(_DEFAULT_CONNECTION_RELATIONS)
        parts.append(
            "\n## Suggested Connection Relation Vocabulary (hint only, not exhaustive — "
            "prefer one of these for connections[].relation, but a short free-text "
            "description is fine if none fit)"
        )
        parts.append(json.dumps(relation_types, ensure_ascii=False))

        parts.append("\n## Output Schema")
        parts.append(json.dumps(_OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        parts.append("\n## Allowed Values")
        parts.append(f"match_status: {', '.join(MATCH_STATUSES)}")
        parts.append(f"suggested_mode: {', '.join(FIGURE_MODES)}")
        parts.append("\n## Constraints")
        parts.append(
            "- Never classify an image as functional_diagram merely because this pipeline stage has "
            "an apparatus-oriented legacy name\n"
            "- data_plot analysis must separate directly visible observations from interpretation\n"
            "- descriptive_image analysis explains visible subjects/regions and must not invent connections\n"
            "- mixed is for genuinely mixed panels; include panels[] with a mode per panel\n"
            "- unknown is preferred when evidence is insufficient\n"
            "- For modes other than functional_diagram (and functional panels in mixed), leave "
            "apparatus_name_candidate empty, set match_status=unknown, set "
            "matched_library_entry_id=null, matched_library_version_no=null, and leave "
            "parts/connections empty\n"
            "- matched_library_entry_id must be one of the candidates' entry_id values above, "
            "or null when match_status is not 'matched'\n"
            "- evidence_quote fields must be verbatim substrings of the caption or nearby text "
            "(or an empty string if nothing supports it — never invent a quote)\n"
            "- from_part / to_part in connections must reference a name from parts[]\n"
            "- confidence must be between 0.0 and 1.0\n"
            "- For every in-figure label that denotes a physical component, output exactly one "
            "part with label_ref set to that label string verbatim. If caption/nearby text do "
            "not let you determine its role, still output the part with role=\"\" and a low "
            "confidence — do not drop it.\n"
            "- Parameter/annotation-style labels (e.g. 'f = 75 mm', 's-pol.') are not components "
            "and may be skipped — do not force a part for these.\n"
            "- Visible parts with no corresponding in-figure label must have label_ref=null.\n"
            "- role must be backed by a verbatim evidence_quote from the caption/nearby text. "
            "If the body text gives no basis for a role, leave role empty and put any "
            "appearance-based guess in reason instead — never assert an unsupported role.\n"
            "- Do NOT output bbox or expanded_name for parts — these are attached "
            "deterministically downstream from label_ref, not by you.\n"
            "- Return ONLY valid JSON, no markdown fences"
        )
        return "\n".join(parts)
