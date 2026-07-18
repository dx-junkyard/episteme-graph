"""Tests for ``IterativePromptFactory`` (Wave 1 scaffolding).

Design doc: docs/features/contextual_figure_analysis_iterative_verification.md.

Covers: the hypothesis step's "no image" constraint, the observation step's
structural exclusion of caption/nearby_text (semantic-priming isolation), the
alignment step's "text_only must not appear in parts" constraint, that the
verification step's prompt embeds the caller-supplied task questions, and
that teacher guidance appears only in the alignment/verification prompts —
never in hypothesis/observation.

This file is additive — it does not modify or re-test anything already
covered by test_prompt_guidance.py (which exercises
``ApparatusSemanticsPromptFactory``, unchanged in this wave).
"""
from __future__ import annotations

from episteme_graph.agents.apparatus_semantics.prompt import IterativePromptFactory
from episteme_graph.agents.apparatus_semantics.schema import FigureImageInput

FACTORY = IterativePromptFactory()

_UNIQUE_CAPTION = "A very unique caption token XCAPTIONX for isolation testing."
_UNIQUE_NEARBY = "A very unique nearby-text token XNEARBYX for isolation testing."


def _figure(**overrides) -> FigureImageInput:
    defaults = dict(
        figure_id="fig_1",
        figure_key="fig_1",
        figure_label="Figure 1",
        caption_text=_UNIQUE_CAPTION,
        image_bytes=b"\x89PNG\r\n\x1a\nimage",
        nearby_text=[_UNIQUE_NEARBY],
    )
    defaults.update(overrides)
    return FigureImageInput(**defaults)


# ---------------------------------------------------------------------------
# Step 1: hypothesis (text-only, no image)
# ---------------------------------------------------------------------------


def test_hypothesis_messages_state_no_image_constraint():
    messages = FACTORY.build_hypothesis_messages(_figure())
    system_content = messages[0]["content"]
    user_content = messages[1]["content"]
    assert "NOT see the image" in system_content
    assert "NOT see the image" in user_content


def test_hypothesis_messages_include_caption_and_nearby_text():
    messages = FACTORY.build_hypothesis_messages(_figure())
    content = messages[1]["content"]
    assert _UNIQUE_CAPTION in content
    assert _UNIQUE_NEARBY in content


def test_hypothesis_messages_include_cartridge_hints_when_given():
    messages = FACTORY.build_hypothesis_messages(
        _figure(), cartridge_hints={"component_types": ["laser"], "aliases": {"L1": "laser"}},
    )
    content = messages[1]["content"]
    assert "laser" in content


def test_hypothesis_messages_have_no_guidance_section():
    """build_hypothesis_messages has no guidance parameter at all — teacher
    guidance must never leak into the text-only hypothesis step."""
    messages = FACTORY.build_hypothesis_messages(_figure())
    content = messages[1]["content"]
    assert "Teacher Guidance" not in content


def test_hypothesis_output_schema_mentions_expected_elements_and_relations():
    messages = FACTORY.build_hypothesis_messages(_figure())
    content = messages[1]["content"]
    assert "expected_elements" in content
    assert "expected_relations" in content
    assert "falsification_conditions" in content


# ---------------------------------------------------------------------------
# Step 2: observation (image-only, semantic context withheld)
# ---------------------------------------------------------------------------


def test_observation_messages_exclude_caption_and_nearby_text():
    """Structural check for the design doc's semantic-priming isolation:
    the observation step must never see caption/nearby_text content."""
    messages = FACTORY.build_observation_messages(_figure())
    content = messages[1]["content"]
    assert _UNIQUE_CAPTION not in content
    assert _UNIQUE_NEARBY not in content
    system_content = messages[0]["content"]
    assert _UNIQUE_CAPTION not in system_content
    assert _UNIQUE_NEARBY not in system_content


def test_observation_messages_exclude_abbreviations_and_figure_record():
    figure = _figure(
        abbreviations={"L1": "laser one"},
        figure_record={"caption": "should not leak", "figure_type": "diagram"},
    )
    messages = FACTORY.build_observation_messages(figure)
    content = messages[1]["content"]
    assert "laser one" not in content
    assert "should not leak" not in content


def test_observation_messages_include_inner_label_text_only():
    figure = _figure(inner_labels=[{"text": "L1", "bbox": [0, 0, 1, 1]}])
    messages = FACTORY.build_observation_messages(figure)
    content = messages[1]["content"]
    assert "L1" in content
    # bbox coordinates are a page-coordinate fact, not something the vision
    # step should be primed with as if it were a fabricated pixel location.
    assert "[0, 0, 1, 1]" not in content


def test_observation_messages_forbid_meaning_interpretation():
    messages = FACTORY.build_observation_messages(_figure())
    content = messages[0]["content"] + messages[1]["content"]
    assert "do not interpret meaning" in content.lower()


def test_observation_messages_have_no_guidance_section():
    """build_observation_messages has no guidance parameter at all."""
    messages = FACTORY.build_observation_messages(_figure())
    content = messages[1]["content"]
    assert "Teacher Guidance" not in content


def test_observation_output_schema_lists_figure_modes_for_visual_mode_guess():
    messages = FACTORY.build_observation_messages(_figure())
    content = messages[1]["content"]
    assert "functional_diagram" in content
    assert "data_plot" in content
    assert "descriptive_image" in content


# ---------------------------------------------------------------------------
# Step 3: alignment (text-only reconciliation)
# ---------------------------------------------------------------------------


def test_alignment_messages_forbid_text_only_items_in_parts():
    messages = FACTORY.build_alignment_messages(_figure(), {"role_in_paper": "x"}, {"elements": []})
    content = messages[0]["content"] + messages[1]["content"]
    assert "text_only" in content
    assert "MUST NOT appear" in content


def test_alignment_messages_include_hypothesis_and_observations_payload():
    hypothesis = {"role_in_paper": "unique_hypothesis_marker"}
    observations = {"elements": [{"observation_id": "unique_observation_marker"}]}
    messages = FACTORY.build_alignment_messages(_figure(), hypothesis, observations)
    content = messages[1]["content"]
    assert "unique_hypothesis_marker" in content
    assert "unique_observation_marker" in content


def test_alignment_messages_include_candidate_briefs_when_given():
    messages = FACTORY.build_alignment_messages(
        _figure(), {}, {}, candidate_briefs=[{"entry_id": "lib_1", "name": "Widget"}],
    )
    content = messages[1]["content"]
    assert "Widget" in content


def test_alignment_messages_no_candidates_message_when_empty():
    messages = FACTORY.build_alignment_messages(_figure(), {}, {}, candidate_briefs=[])
    content = messages[1]["content"]
    assert "None retrieved" in content


def test_alignment_messages_forbid_shape_similarity_as_direct_evidence():
    messages = FACTORY.build_alignment_messages(_figure(), {}, {})
    content = messages[0]["content"] + messages[1]["content"]
    assert "never treat it as direct evidence" in content or "never direct evidence" in content


def test_alignment_output_schema_includes_legacy_and_new_fields():
    messages = FACTORY.build_alignment_messages(_figure(), {}, {})
    content = messages[1]["content"]
    for key in (
        "apparatus_name_candidate", "match_status", "parts", "connections",
        "suggested_mode", "mode_reason", "analysis_profile", "evidence_quote",
        "reason", "confidence", "guidance_note",
        "alignment_items", "alternative_hypotheses", "verification_tasks",
        "review_questions",
    ):
        assert key in content, f"missing {key} in alignment output schema"


# ---------------------------------------------------------------------------
# Guidance: alignment and verification only, never hypothesis/observation
# ---------------------------------------------------------------------------


_GUIDANCE = {
    "hint_text": "the left box labeled L1 is the modulator",
    "focus_bbox_rel": None,
    "focus_label_texts": [],
    "has_focus_image": False,
}


def test_alignment_messages_include_guidance_section_when_given():
    messages = FACTORY.build_alignment_messages(_figure(), {}, {}, guidance=_GUIDANCE)
    content = messages[1]["content"]
    assert "Teacher Guidance" in content
    assert "the left box labeled L1 is the modulator" in content
    assert "never quote the teacher's hint as evidence" in content


def test_alignment_messages_omit_guidance_section_when_absent():
    messages = FACTORY.build_alignment_messages(_figure(), {}, {})
    content = messages[1]["content"]
    assert "Teacher Guidance" not in content


def test_alignment_repair_messages_include_guidance_and_previous_output():
    messages = FACTORY.build_alignment_repair_messages(
        _figure(), {}, {}, [], {}, {"match_status": "novel"}, [], guidance=_GUIDANCE,
    )
    content = messages[1]["content"]
    assert "Teacher Guidance" in content
    assert "Previous Output" in content
    assert "novel" in content


def test_verification_messages_include_guidance_section_when_given():
    messages = FACTORY.build_verification_messages(
        _figure(), [{"task_id": "task_1", "question": "is X visible?"}], [], 1, guidance=_GUIDANCE,
    )
    content = messages[1]["content"]
    assert "Teacher Guidance" in content
    assert "never quote the teacher's hint as evidence" in content


def test_verification_messages_omit_guidance_section_when_absent():
    messages = FACTORY.build_verification_messages(
        _figure(), [{"task_id": "task_1", "question": "is X visible?"}], [], 1,
    )
    content = messages[1]["content"]
    assert "Teacher Guidance" not in content


# ---------------------------------------------------------------------------
# Step 5: verification (gap-driven re-scan)
# ---------------------------------------------------------------------------


def test_verification_messages_embed_task_questions():
    tasks = [
        {"task_id": "task_1", "question": "does the second arm connect to the detector?"},
    ]
    messages = FACTORY.build_verification_messages(_figure(), tasks, [], 1)
    content = messages[1]["content"]
    assert "does the second arm connect to the detector?" in content


def test_verification_messages_embed_iteration_index():
    messages = FACTORY.build_verification_messages(_figure(), [], [], 2)
    content = messages[1]["content"]
    assert "iteration 2" in content


def test_verification_messages_embed_alignment_snapshot():
    snapshot = [{"item_id": "align_1", "status": "text_only", "label": "unique_snapshot_marker"}]
    messages = FACTORY.build_verification_messages(_figure(), [], snapshot, 1)
    content = messages[1]["content"]
    assert "unique_snapshot_marker" in content


def test_verification_messages_forbid_repeating_prior_questions():
    messages = FACTORY.build_verification_messages(_figure(), [], [], 1)
    content = messages[0]["content"] + messages[1]["content"]
    assert "repeat" in content.lower() or "already asked" in content.lower()


def test_verification_messages_require_visual_grounding_for_additions():
    messages = FACTORY.build_verification_messages(_figure(), [], [], 1)
    content = messages[0]["content"] + messages[1]["content"]
    assert "without visual" in content.lower() or "visual grounding" in content.lower()


def test_verification_output_schema_includes_record_deltas():
    messages = FACTORY.build_verification_messages(_figure(), [], [], 1)
    content = messages[1]["content"]
    for key in (
        "task_findings", "updated_alignment_items", "new_alignment_items",
        "new_verification_tasks", "hypothesis_updates", "record_deltas", "notes",
        "parts_to_add", "parts_to_remove", "connections_to_add", "connections_to_remove",
    ):
        assert key in content, f"missing {key} in verification output schema"
