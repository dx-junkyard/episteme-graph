"""Prompt construction for ApparatusSemanticsAgent.

Domain-independent by design (principle #5): apparatus vocabulary is never
hardcoded here. Candidate briefs (from the frozen library, §5-3) and cartridge
hints are supplied as few-shot knowledge only; the model is always allowed to
answer novel/unknown when nothing matches.
"""
from __future__ import annotations

import json

from .schema import (
    ALIGNMENT_SEVERITIES,
    ALIGNMENT_STATUSES,
    HYPOTHESIS_STATUSES,
    MATCH_STATUSES,
    FigureImageInput,
)
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
    "guidance_note": "how the teacher guidance was applied, or why the requested element could not be found; empty string when no guidance was given",
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
        guidance: dict | None = None,
    ) -> list[dict]:
        return [
            {"role": "system", "content": _SYSTEM_CONTENT},
            {"role": "user", "content": self._build_user_content(
                figure, candidate_briefs, nearby_text, cartridge_hints,
                inner_label_hints, abbreviations, guidance,
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
        guidance: dict | None = None,
    ) -> list[dict]:
        issue_text = "\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        )
        content = (
            self._build_user_content(
                figure, candidate_briefs, nearby_text, cartridge_hints,
                inner_label_hints, abbreviations, guidance,
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
        guidance: dict | None = None,
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

        if guidance:
            parts.extend(self._build_guidance_section(guidance))

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

    @staticmethod
    def _build_guidance_section(guidance: dict) -> list[str]:
        """Render the "## Teacher Guidance" section (guided_figure_reanalysis_design.md §6-3).

        Only called when ``guidance`` is a non-empty dict — un-guided calls
        (including every batch-pipeline figure, GF7) never see this section
        at all. GF2 is enforced here structurally: the rules text explicitly
        forbids treating the teacher's hint as an evidence_quote source.
        """
        lines: list[str] = [
            "\n## Teacher Guidance (attention directive from a human reviewer)"
        ]
        hint_text = guidance.get("hint_text") or ""
        if hint_text:
            lines.append(f"hint: {hint_text}")

        focus_bbox_rel = guidance.get("focus_bbox_rel")
        if focus_bbox_rel:
            lines.append(
                "focus_region (relative coords in the first image): "
                + json.dumps(focus_bbox_rel, ensure_ascii=False)
            )
            if guidance.get("has_focus_image"):
                lines.append(
                    "The second attached image is a magnified crop of this focus region."
                )
            focus_label_texts = guidance.get("focus_label_texts") or []
            labels_display = (
                ", ".join(focus_label_texts) if focus_label_texts else "unavailable"
            )
            lines.append(f"In-figure labels inside the focus region: {labels_display}")

        lines.append("\nRules for using this guidance:")
        lines.append(
            "- The guidance directs your attention; it is NOT evidence. evidence_quote "
            "fields must still be verbatim substrings of the caption/nearby text only — "
            "never quote the teacher's hint as evidence.\n"
            "- Prioritize detecting components inside the focus region, but do not delete "
            "or contradict correct findings outside it.\n"
            "- In the output field \"guidance_note\", state briefly how you applied the "
            "guidance. If you could not find what the teacher pointed at, say so "
            "explicitly — do not fabricate a detection to satisfy the hint."
        )
        return lines


# =============================================================================
# Iterative contextual-analysis pipeline
# (docs/features/contextual_figure_analysis_iterative_verification.md)
#
# ``IterativePromptFactory`` is additive: ``ApparatusSemanticsPromptFactory``
# above is unmodified and remains the one-shot prompt path used by agent.py
# until the state machine (a later wave) switches over. The four steps below
# are deliberately separate prompts with separate information diets — the
# hypothesis step never sees the image, the observation step never sees
# caption/nearby_text/candidates (to avoid semantic priming), and only the
# alignment/verification steps see teacher guidance.
# =============================================================================

_HYPOTHESIS_SYSTEM_CONTENT = """\
You are building a text-only expectation model for a figure in a scientific \
paper. You will NOT see the image in this step. Do not claim that any \
element, shape, or label exists in the image — describe only what the \
surrounding text leads a reader to expect.

Read the caption, the paragraphs that reference this figure, and the \
abbreviation dictionary. Infer:
1. The role this figure plays in the paper's argument
2. What the figure as a whole is expected to depict
3. The primary/secondary elements a reader would expect to see, each backed \
   by a verbatim quote from the text when the text supports it
4. The relations/connections/direction expected between those elements
5. Labels, shapes, or visual cues the text leads you to expect in the image
6. Points the text leaves unstated (say plainly what you cannot infer)
7. Conditions that, if contradicted by the image, would falsify this expectation

Evidence discipline: every expected element/relation should carry an \
evidence_quote that is a verbatim substring of the caption or nearby text, or \
an empty string if nothing in the text supports it directly (a weaker \
inference may still go in "reason").

Return ONLY valid JSON matching the output schema — no markdown fences.
"""

_HYPOTHESIS_OUTPUT_SCHEMA = {
    "role_in_paper": "string",
    "overall_subject": "string",
    "expected_elements": [{
        "element_id": "exp_1",
        "name": "string",
        "evidence_quote": "verbatim quote from caption/nearby text, or empty string",
        "expected_labels": ["label strings expected to appear in the figure"],
        "importance": "primary | secondary",
        "confidence": 0.0,
    }],
    "expected_relations": [{
        "relation_id": "rel_1",
        "from_element_id": "an expected_elements[].element_id",
        "to_element_id": "an expected_elements[].element_id",
        "relation": "verb phrase describing the relation",
        "direction": "from->to | bidirectional | unknown",
        "evidence_quote": "verbatim quote, or empty string",
        "confidence": 0.0,
    }],
    "expected_visual_cues": ["shapes/labels/visual patterns the text leads you to expect"],
    "unstated_points": ["points the text does not make explicit"],
    "falsification_conditions": ["conditions that would contradict this hypothesis if seen in the image"],
    "evidence_quotes": ["overall supporting verbatim quotes"],
    "reason": "string",
    "confidence": 0.0,
}

_OBSERVATION_SYSTEM_CONTENT = """\
You are looking ONLY at an image cropped from a scientific paper's figure. \
You have NOT been given the caption, surrounding text, or any other semantic \
context, so as not to bias what you report.

Report only what is directly visible: panels, boxes, lines, arrows, axes, \
legends, label strings, connection directions, and repeated shapes/motifs. \
Separate what you SEE from what it might MEAN — do not interpret meaning or \
guess a function/role for anything. Mark unreadable regions and elements you \
cannot classify honestly rather than guessing.

Do not fabricate coordinates: region_hint is a short textual description of \
position (e.g. "upper-left", "center panel"), never a fabricated pixel or \
relative coordinate.

Return ONLY valid JSON matching the output schema — no markdown fences.
"""

_OBSERVATION_OUTPUT_SCHEMA = {
    "panels": [{"panel_id": "string", "description": "string", "region_hint": "string"}],
    "elements": [{
        "observation_id": "obs_1",
        "kind": "box | arrow | line | axis | legend | label | panel | region | symbol | other",
        "description": "visual description only, no interpretation of meaning",
        "label_text": "text actually readable at this element, or empty string",
        "region_hint": "textual position description, e.g. 'upper-left'",
    }],
    "connections": [{
        "from_observation_id": "an elements[].observation_id",
        "to_observation_id": "an elements[].observation_id",
        "connector": "arrow | line | adjacency | other",
        "direction": "from->to | bidirectional | unknown",
        "description": "string",
    }],
    "ocr_labels": ["text strings read directly off the image"],
    "repeated_motifs": [{"description": "string", "observation_ids": ["obs_1", "obs_2"]}],
    "unreadable_regions": [{"region_hint": "string", "reason": "string"}],
    "undecidable_elements": [{"observation_id": "obs_N", "note": "why it cannot be classified"}],
    "visual_mode_guess": "functional_diagram | data_plot | descriptive_image | mixed | unknown",
    "reason": "string",
    "confidence": 0.0,
}

_ALIGNMENT_SYSTEM_CONTENT = """\
You are reconciling a text-only expectation model with an independent visual \
observation of the same figure, to produce a final identification plus an \
explicit map of where the two sources agree, disagree, or leave gaps.

Every part you emit in the final record MUST be backed by the visual \
observations (via observation_refs) or an in-figure label (via label_ref). An \
element that appears only in the text expectation and has no visual backing \
MUST become an alignment item with status "text_only" and MUST NOT appear in \
the final parts/connections. Shape or motif similarity is only a prior for \
generating a verification task — never treat it as direct evidence of function.

For every element/relation from the hypothesis or the observations, output \
one alignment item with a status:
- supported_by_both: confirmed by both the text expectation and the image
- visual_only: seen in the image, no textual meaning confirmed yet
- text_only: expected from text, not confirmed in the image
- contradicted: text and image disagree
- unresolved: neither source is sufficient to decide

When the alignment is genuinely ambiguous, keep 2-3 alternative hypotheses \
open rather than forcing a single answer; record their supporting/counter \
evidence and unverified conditions.

For every text_only / contradicted / unresolved item that matters, propose a \
verification task: a specific question, target region, and success/refutation \
condition for a follow-up image inspection. Do not propose the same question \
twice.

Return ONLY valid JSON matching the output schema — no markdown fences.
"""

_ALIGNMENT_ITEM_SCHEMA = {
    "item_id": "align_1",
    "item_kind": "element | relation",
    "label": "short human-readable name",
    "status": "supported_by_both | visual_only | text_only | contradicted | unresolved",
    "expected_ref": "a context_hypothesis element_id/relation_id, or empty string",
    "observation_refs": ["visual_observations elements[].observation_id"],
    "label_ref": "an in-figure label verbatim, or empty string",
    "text_evidence": "verbatim quote from caption/nearby text, or empty string",
    "visual_evidence": "description drawn from the visual observations, or empty string",
    "severity": "high | medium | low",
    "reason": "string",
    "confidence": 0.0,
}

_VERIFICATION_TASK_SCHEMA = {
    "task_id": "task_1",
    "question": "specific verification question",
    "target_item_ids": ["an alignment_items[].item_id"],
    "region_hint": "string",
    "focus_bbox_rel": None,
    "success_condition": "what would resolve this task",
    "refutation_condition": "what would refute the related item",
}

_ALIGNMENT_OUTPUT_SCHEMA = {
    **_OUTPUT_SCHEMA,
    "alignment_items": [_ALIGNMENT_ITEM_SCHEMA],
    "alternative_hypotheses": [{
        "hypothesis_id": "hyp_1",
        "description": "string",
        "supporting_evidence": ["string"],
        "counter_evidence": ["string"],
        "unverified_conditions": ["string"],
        "status": "active | rejected | selected",
        "confidence": 0.0,
    }],
    "verification_tasks": [_VERIFICATION_TASK_SCHEMA],
    "review_questions": [{
        "question_id": "q_1",
        "question": "a concrete question for a human reviewer",
        "related_item_ids": ["an alignment_items[].item_id"],
        "region_hint": "string",
    }],
}

_VERIFICATION_SYSTEM_CONTENT = """\
You are re-examining a figure image to answer specific, targeted verification \
tasks raised by a previous alignment step. Answer ONLY the listed \
verification tasks by looking at the image again — do not re-derive the \
whole analysis from scratch.

For each task, return an outcome (resolved | refuted | unresolved) with the \
evidence you actually see in the image. Then return only the alignment items \
that changed as a result (updated_alignment_items), any brand-new alignment \
items you discovered while checking (new_alignment_items), updated status/\
confidence for any alternative hypothesis affected (hypothesis_updates), new \
verification tasks ONLY if they ask something not already asked before (do \
not repeat a prior question), and the concrete deltas to the final record \
(record_deltas: parts_to_add / parts_to_remove / connections_to_add / \
connections_to_remove). Every addition in record_deltas MUST cite the \
observation/label evidence that justifies it — never add a part or \
connection without visual grounding.

Return ONLY valid JSON matching the output schema — no markdown fences.
"""

_VERIFICATION_OUTPUT_SCHEMA = {
    "task_findings": [{
        "task_id": "a tasks[].task_id from below",
        "outcome": "resolved | refuted | unresolved",
        "observation": "what you actually see now",
        "evidence": "verbatim label or description backing the observation",
    }],
    "updated_alignment_items": [_ALIGNMENT_ITEM_SCHEMA],
    "new_alignment_items": [_ALIGNMENT_ITEM_SCHEMA],
    "new_verification_tasks": [_VERIFICATION_TASK_SCHEMA],
    "hypothesis_updates": [{
        "hypothesis_id": "an alternative_hypotheses[].hypothesis_id",
        "status": "active | rejected | selected",
        "confidence": 0.0,
        "reason": "string",
    }],
    "record_deltas": {
        "parts_to_add": [{
            "name": "string", "role": "string", "label_ref": "string or null",
            "evidence_quote": "string", "reason": "string", "confidence": 0.0,
            "observation_refs": ["obs_id backing this addition"],
        }],
        "parts_to_remove": ["part name to remove"],
        "connections_to_add": [{
            "from_part": "string", "to_part": "string", "relation": "string",
            "reason": "string", "confidence": 0.0,
        }],
        "connections_to_remove": [{"from_part": "string", "to_part": "string"}],
    },
    "notes": "string",
}


class IterativePromptFactory:
    """Prompt builder for the contextual hypothesis / independent observation /
    alignment / gap-driven verification pipeline.

    Kept separate from ``ApparatusSemanticsPromptFactory`` above — the
    one-shot prompt factory is unmodified and still used until the state
    machine (a later wave) switches the agent over to this pipeline.
    """

    def build_hypothesis_messages(
        self,
        figure: FigureImageInput,
        cartridge_hints: dict | None = None,
    ) -> list[dict]:
        """Text-only step 1 prompt. No image is attached by the caller for
        this step, and this prompt states explicitly that the model must not
        assume anything about the (unseen) image."""
        parts: list[str] = ["## Task"]
        parts.append(
            "Build a text-only expectation model for the figure described below. "
            "Return ONLY JSON matching the output schema."
        )
        parts.append("\n## Figure Context (text only — you will NOT see the image)")
        parts.append(f"figure_id: {figure.figure_id}")
        parts.append(f"figure_key: {figure.figure_key}")
        if figure.figure_label:
            parts.append(f"figure_label: {figure.figure_label}")
        parts.append(f"caption: {figure.caption_text}")
        if figure.figure_record:
            parts.append("figure_record (from FigureTableSemanticsAgent):")
            parts.append(json.dumps(figure.figure_record, ensure_ascii=False, indent=2))
        if figure.nearby_text:
            parts.append("\n## Nearby Text")
            parts.extend(f"- {t}" for t in figure.nearby_text)
        if figure.abbreviations:
            parts.append("\n## Abbreviation Dictionary (from the paper body)")
            parts.append(json.dumps(figure.abbreviations, ensure_ascii=False, indent=2))
        if cartridge_hints and cartridge_hints.get("component_types"):
            parts.append("\n## Cartridge Component-Type Vocabulary (hint only, not exhaustive)")
            parts.append(json.dumps(cartridge_hints["component_types"][:30], ensure_ascii=False))
        if cartridge_hints and cartridge_hints.get("aliases"):
            parts.append("\n## Cartridge Term Aliases (hint only)")
            parts.append(json.dumps(cartridge_hints["aliases"], ensure_ascii=False))

        parts.append("\n## Output Schema")
        parts.append(json.dumps(_HYPOTHESIS_OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        parts.append("\n## Constraints")
        parts.append(
            "- You will NOT see the image in this step. Do not claim any element, "
            "shape, or label exists in the image; describe only what the text "
            "leads you to expect.\n"
            "- importance must be 'primary' or 'secondary'\n"
            "- evidence_quote fields must be verbatim substrings of the caption or "
            "nearby text, or an empty string — never invent a quote\n"
            "- from_element_id / to_element_id in expected_relations must reference "
            "an expected_elements[].element_id\n"
            "- confidence must be between 0.0 and 1.0\n"
            "- Return ONLY valid JSON, no markdown fences"
        )
        content = "\n".join(parts)
        return [
            {"role": "system", "content": _HYPOTHESIS_SYSTEM_CONTENT},
            {"role": "user", "content": content},
        ]

    def build_observation_messages(self, figure: FigureImageInput) -> list[dict]:
        """Image-only step 2 prompt: caption/nearby_text/abbreviations/
        figure_record/library candidates are deliberately withheld to
        minimize semantic priming (design doc §2). Only in-figure label text
        is included — it is a deterministic fact from the PDF text layer
        (figure_image_extraction stage), not an interpretation.
        """
        parts: list[str] = ["## Task"]
        parts.append(
            "Report only what is directly visible in the attached image. "
            "Return ONLY JSON matching the output schema."
        )
        parts.append(f"\nfigure_id: {figure.figure_id}")

        inner_label_texts = self._inner_label_texts(figure)
        if inner_label_texts:
            parts.append(
                "\n## In-Figure Label Strings (extracted deterministically from the "
                "PDF text layer — a factual list of text present in the image, "
                "not an interpretation)"
            )
            parts.extend(f"- {t}" for t in inner_label_texts)

        parts.append("\n## Output Schema")
        parts.append(json.dumps(_OBSERVATION_OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        parts.append(f"\n## Allowed Values\nvisual_mode_guess: {', '.join(FIGURE_MODES)}")
        parts.append("\n## Constraints")
        parts.append(
            "- Report only what is visible; do not interpret meaning or guess function/role\n"
            "- region_hint is a short text description of position, never a fabricated coordinate\n"
            "- Mark unreadable_regions / undecidable_elements honestly instead of guessing\n"
            "- from_observation_id / to_observation_id in connections must reference an "
            "elements[].observation_id\n"
            "- confidence must be between 0.0 and 1.0\n"
            "- Return ONLY valid JSON, no markdown fences"
        )
        content = "\n".join(parts)
        return [
            {"role": "system", "content": _OBSERVATION_SYSTEM_CONTENT},
            {"role": "user", "content": content},
        ]

    def build_alignment_messages(
        self,
        figure: FigureImageInput,
        hypothesis: dict,
        observations: dict,
        candidate_briefs: list[dict] | None = None,
        cartridge_hints: dict | None = None,
        guidance: dict | None = None,
    ) -> list[dict]:
        content = self._build_alignment_user_content(
            figure, hypothesis, observations, candidate_briefs, cartridge_hints, guidance,
        )
        return [
            {"role": "system", "content": _ALIGNMENT_SYSTEM_CONTENT},
            {"role": "user", "content": content},
        ]

    def build_alignment_repair_messages(
        self,
        figure: FigureImageInput,
        hypothesis: dict,
        observations: dict,
        candidate_briefs: list[dict] | None,
        cartridge_hints: dict | None,
        previous_output: dict,
        issues: list,
        guidance: dict | None = None,
    ) -> list[dict]:
        issue_text = "\n".join(
            f"- [{i.severity}] {i.rule_id}: {i.message}" for i in issues
        )
        content = (
            self._build_alignment_user_content(
                figure, hypothesis, observations, candidate_briefs, cartridge_hints, guidance,
            )
            + "\n\n## Previous Output\n"
            + json.dumps(previous_output, ensure_ascii=False, indent=2)
            + "\n\n## Validation Issues\n"
            + issue_text
            + "\nReturn corrected JSON for this same figure."
        )
        return [
            {"role": "system", "content": _ALIGNMENT_SYSTEM_CONTENT},
            {"role": "user", "content": content},
        ]

    def build_verification_messages(
        self,
        figure: FigureImageInput,
        tasks: list[dict],
        alignment_snapshot: list[dict],
        iteration_index: int,
        guidance: dict | None = None,
    ) -> list[dict]:
        """Image-only step 5 (gap-driven re-verification) prompt: focused on
        the outstanding tasks and the current alignment snapshot only — it
        does not re-send the full hypothesis/observation context."""
        parts: list[str] = ["## Task"]
        parts.append(
            f"This is verification iteration {iteration_index}. Answer ONLY the "
            "listed verification tasks by re-examining the attached image. "
            "Return ONLY JSON matching the output schema."
        )
        parts.append(f"\nfigure_id: {figure.figure_id}")

        parts.append("\n## Verification Tasks")
        parts.append(json.dumps(tasks, ensure_ascii=False, indent=2))

        parts.append("\n## Current Alignment Snapshot")
        parts.append(json.dumps(alignment_snapshot, ensure_ascii=False, indent=2))

        inner_label_texts = self._inner_label_texts(figure)
        if inner_label_texts:
            parts.append("\n## In-Figure Label Strings (from the PDF text layer)")
            parts.extend(f"- {t}" for t in inner_label_texts)

        if guidance:
            parts.extend(ApparatusSemanticsPromptFactory._build_guidance_section(guidance))

        parts.append("\n## Output Schema")
        parts.append(json.dumps(_VERIFICATION_OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        parts.append("\n## Allowed Values")
        parts.append("task_findings[].outcome: resolved, refuted, unresolved")
        parts.append(f"alignment item status: {', '.join(ALIGNMENT_STATUSES)}")
        parts.append(f"alternative hypothesis status: {', '.join(HYPOTHESIS_STATUSES)}")
        parts.append("\n## Constraints")
        parts.append(
            "- Answer ONLY the tasks listed above; do not re-derive the whole analysis\n"
            "- Do not propose a new_verification_task that repeats a question already asked\n"
            "- Every entry in record_deltas.parts_to_add must cite observation_refs or a "
            "label_ref that grounds it in what is visible now — never add a part without "
            "visual evidence\n"
            "- confidence must be between 0.0 and 1.0\n"
            "- Return ONLY valid JSON, no markdown fences"
        )
        content = "\n".join(parts)
        return [
            {"role": "system", "content": _VERIFICATION_SYSTEM_CONTENT},
            {"role": "user", "content": content},
        ]

    def _build_alignment_user_content(
        self,
        figure: FigureImageInput,
        hypothesis: dict,
        observations: dict,
        candidate_briefs: list[dict] | None,
        cartridge_hints: dict | None,
        guidance: dict | None,
    ) -> str:
        parts: list[str] = ["## Task"]
        parts.append(
            "Reconcile the context hypothesis (text-only) with the visual "
            "observations (image-only) below for this figure. Return ONLY "
            "JSON matching the output schema."
        )
        parts.append("\n## Figure Context")
        parts.append(f"figure_id: {figure.figure_id}")
        parts.append(f"figure_key: {figure.figure_key}")
        if figure.figure_label:
            parts.append(f"figure_label: {figure.figure_label}")
        parts.append(f"caption: {figure.caption_text}")

        if figure.nearby_text:
            parts.append("\n## Nearby Text")
            parts.extend(f"- {t}" for t in figure.nearby_text)

        inner_label_texts = self._inner_label_texts(figure)
        if inner_label_texts:
            parts.append("\n## In-Figure Labels (from the PDF text layer)")
            parts.extend(f"- {t}" for t in inner_label_texts)

        if figure.abbreviations:
            parts.append("\n## Abbreviation Dictionary (from the paper body)")
            parts.append(json.dumps(figure.abbreviations, ensure_ascii=False, indent=2))

        parts.append("\n## Context Hypothesis (text-only, from the previous step)")
        parts.append(json.dumps(hypothesis, ensure_ascii=False, indent=2))

        parts.append("\n## Visual Observations (image-only, from the previous step)")
        parts.append(json.dumps(observations, ensure_ascii=False, indent=2))

        if guidance:
            parts.extend(ApparatusSemanticsPromptFactory._build_guidance_section(guidance))

        if candidate_briefs:
            parts.append(
                "\n## Library Candidates (retrieved from the frozen knowledge "
                "library — choose one only if the image clearly matches; "
                "otherwise use match_status='novel' or 'unknown')"
            )
            parts.append(json.dumps(candidate_briefs, ensure_ascii=False, indent=2))
        else:
            parts.append(
                "\n## Library Candidates\nNone retrieved. Use match_status='novel' "
                "(or 'unknown' if the apparatus cannot be identified)."
            )

        if cartridge_hints and cartridge_hints.get("component_types"):
            parts.append("\n## Cartridge Component-Type Vocabulary (hint only, not exhaustive)")
            parts.append(json.dumps(cartridge_hints["component_types"][:30], ensure_ascii=False))
        if cartridge_hints and cartridge_hints.get("aliases"):
            parts.append("\n## Cartridge Term Aliases (hint only)")
            parts.append(json.dumps(cartridge_hints["aliases"], ensure_ascii=False))

        parts.append("\n## Output Schema")
        parts.append(json.dumps(_ALIGNMENT_OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        parts.append("\n## Allowed Values")
        parts.append(f"match_status: {', '.join(MATCH_STATUSES)}")
        parts.append(f"suggested_mode: {', '.join(FIGURE_MODES)}")
        parts.append(f"alignment_items[].status: {', '.join(ALIGNMENT_STATUSES)}")
        parts.append(f"alignment_items[].severity: {', '.join(ALIGNMENT_SEVERITIES)}")
        parts.append(f"alternative_hypotheses[].status: {', '.join(HYPOTHESIS_STATUSES)}")
        parts.append("\n## Constraints")
        parts.append(
            "- Every part in the final record MUST be backed by observation_refs or a "
            "label_ref grounded in the visual observations; an element seen only in the "
            "text hypothesis MUST become a text_only alignment item and MUST NOT appear "
            "in parts/connections\n"
            "- Shape/motif similarity alone is never direct evidence of function — use it "
            "only to justify a verification task\n"
            "- text_evidence must be a verbatim substring of the caption/nearby text, or "
            "an empty string\n"
            "- label_ref must be one of the in-figure labels verbatim, or empty string\n"
            "- Do not propose a verification_task that repeats a question already implied "
            "by a previous alignment item's resolved status\n"
            "- Every verification_task must have a question, success_condition, and "
            "refutation_condition\n"
            "- confidence must be between 0.0 and 1.0\n"
            "- Return ONLY valid JSON, no markdown fences"
        )
        return "\n".join(parts)

    @staticmethod
    def _inner_label_texts(figure: FigureImageInput) -> list[str]:
        texts: list[str] = []
        seen: set[str] = set()
        for label in figure.inner_labels or []:
            if not isinstance(label, dict):
                continue
            text = str(label.get("text", "") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            texts.append(text)
        return texts
