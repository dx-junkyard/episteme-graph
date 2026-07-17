"""Tests for the "## Teacher Guidance" prompt section.

Design doc: docs/features/guided_figure_reanalysis_design.md §6-3.
Covers: the guidance section only appearing when guidance is a non-empty
dict, the GF2 evidence-quote prohibition wording (structural check that the
prompt tells the model never to quote the teacher's hint as evidence), the
"unavailable" fallback when focus_label_texts is empty, and that
build_repair_messages surfaces the same section (it shares
_build_user_content with build_messages, so this also guards against future
refactors that break that sharing).

This file is additive to test_label_grounding.py's existing prompt.py
coverage (inner_label_hints / abbreviations sections) — it only exercises the
new guidance parameter.
"""
from __future__ import annotations

from episteme_graph.agents.apparatus_semantics.prompt import ApparatusSemanticsPromptFactory
from episteme_graph.agents.apparatus_semantics.schema import FigureImageInput

FACTORY = ApparatusSemanticsPromptFactory()


def _figure(figure_id: str = "fig_1") -> FigureImageInput:
    return FigureImageInput(
        figure_id=figure_id,
        figure_key=figure_id,
        figure_label="Figure 1",
        caption_text="Schematic of the setup.",
        image_bytes=b"\x89PNG\r\n\x1a\nimage",
    )


# ---------------------------------------------------------------------------
# Section presence toggle
# ---------------------------------------------------------------------------


def test_guidance_section_absent_when_guidance_is_none():
    messages = FACTORY.build_messages(_figure(), [], [], {}, guidance=None)
    content = messages[1]["content"]
    assert "Teacher Guidance" not in content


def test_guidance_section_absent_when_guidance_is_empty_dict():
    messages = FACTORY.build_messages(_figure(), [], [], {}, guidance={})
    content = messages[1]["content"]
    assert "Teacher Guidance" not in content


def test_guidance_section_absent_by_default_when_omitted():
    """Existing (pre-guidance) callers that never pass ``guidance=`` must be unaffected."""
    messages = FACTORY.build_messages(_figure(), [], [], {})
    content = messages[1]["content"]
    assert "Teacher Guidance" not in content


def test_guidance_section_present_with_hint_text_only():
    guidance = {
        "hint_text": "the left box labeled EOM is the modulator",
        "focus_bbox_rel": None,
        "focus_label_texts": [],
        "has_focus_image": False,
    }
    messages = FACTORY.build_messages(_figure(), [], [], {}, guidance=guidance)
    content = messages[1]["content"]
    assert "Teacher Guidance" in content
    assert "the left box labeled EOM is the modulator" in content
    # No focus region -> no focus-only lines
    assert "focus_region" not in content
    assert "magnified crop" not in content


def test_guidance_section_present_with_focus_region_and_crop():
    guidance = {
        "hint_text": "",
        "focus_bbox_rel": [0.1, 0.2, 0.5, 0.6],
        "focus_label_texts": ["EOM", "PBS"],
        "has_focus_image": True,
    }
    messages = FACTORY.build_messages(_figure(), [], [], {}, guidance=guidance)
    content = messages[1]["content"]
    assert "Teacher Guidance" in content
    assert "0.1" in content and "0.6" in content
    assert "magnified crop" in content
    assert "EOM" in content and "PBS" in content


# ---------------------------------------------------------------------------
# GF2: never let the teacher's hint be quoted as evidence
# ---------------------------------------------------------------------------


def test_guidance_section_forbids_quoting_hint_as_evidence():
    guidance = {
        "hint_text": "the modulator is bottom-left",
        "focus_bbox_rel": None,
        "focus_label_texts": [],
        "has_focus_image": False,
    }
    messages = FACTORY.build_messages(_figure(), [], [], {}, guidance=guidance)
    content = messages[1]["content"]
    assert "NOT evidence" in content
    assert "never quote the teacher's hint as evidence" in content


def test_guidance_section_instructs_guidance_note_honesty():
    guidance = {"hint_text": "find the detector", "focus_bbox_rel": None,
                "focus_label_texts": [], "has_focus_image": False}
    messages = FACTORY.build_messages(_figure(), [], [], {}, guidance=guidance)
    content = messages[1]["content"]
    assert "guidance_note" in content
    assert "do not fabricate a detection" in content


# ---------------------------------------------------------------------------
# focus_label_texts empty -> "unavailable"
# ---------------------------------------------------------------------------


def test_focus_label_texts_empty_renders_unavailable():
    guidance = {
        "hint_text": "",
        "focus_bbox_rel": [0.0, 0.0, 1.0, 1.0],
        "focus_label_texts": [],
        "has_focus_image": False,
    }
    messages = FACTORY.build_messages(_figure(), [], [], {}, guidance=guidance)
    content = messages[1]["content"]
    assert "In-figure labels inside the focus region: unavailable" in content


def test_focus_label_texts_present_lists_labels():
    guidance = {
        "hint_text": "",
        "focus_bbox_rel": [0.0, 0.0, 1.0, 1.0],
        "focus_label_texts": ["EOM"],
        "has_focus_image": False,
    }
    messages = FACTORY.build_messages(_figure(), [], [], {}, guidance=guidance)
    content = messages[1]["content"]
    assert "In-figure labels inside the focus region: EOM" in content
    assert "unavailable" not in content


# ---------------------------------------------------------------------------
# Output schema mentions guidance_note
# ---------------------------------------------------------------------------


def test_output_schema_mentions_guidance_note():
    messages = FACTORY.build_messages(_figure(), [], [], {})
    assert "guidance_note" in messages[1]["content"]


# ---------------------------------------------------------------------------
# build_repair_messages shares the same guidance rendering
# ---------------------------------------------------------------------------


def test_repair_messages_also_include_guidance_section():
    guidance = {
        "hint_text": "check the bottom-left component",
        "focus_bbox_rel": None,
        "focus_label_texts": [],
        "has_focus_image": False,
    }
    messages = FACTORY.build_repair_messages(
        _figure(), [], [], {}, {"match_status": "novel"}, [],
        guidance=guidance,
    )
    content = messages[1]["content"]
    assert "Teacher Guidance" in content
    assert "check the bottom-left component" in content
    assert "never quote the teacher's hint as evidence" in content


def test_repair_messages_omit_guidance_section_when_absent():
    messages = FACTORY.build_repair_messages(
        _figure(), [], [], {}, {"match_status": "novel"}, [],
    )
    content = messages[1]["content"]
    assert "Teacher Guidance" not in content
