"""Tests for the Wave 1 iterative-pipeline parse functions in repair.py.

Design doc: docs/features/contextual_figure_analysis_iterative_verification.md.

Covers defensiveness: type-mismatch degradation to defaults, confidence
clamping to [0.0, 1.0], and out-of-vocabulary status/severity values being
kept as-is (never silently discarded — validator.py is the layer that turns
those into a reportable issue, design principle #4).

This file is additive — it does not modify or re-test ``_parse_record``/
``_fallback_record`` (already covered by test_agent.py/test_label_grounding.py).
"""
from __future__ import annotations

from episteme_graph.agents.apparatus_semantics.repair import (
    parse_alignment_output,
    parse_hypothesis,
    parse_observations,
    parse_verification_output,
)
from episteme_graph.agents.apparatus_semantics.schema import FigureImageInput


def _figure(**overrides) -> FigureImageInput:
    defaults = dict(
        figure_id="fig_1",
        figure_key="fig_1",
        figure_label="Figure 1",
        caption_text="A schematic of the ion source.",
        image_bytes=b"\x89PNG\r\n\x1a\nrest",
    )
    defaults.update(overrides)
    return FigureImageInput(**defaults)


# ---------------------------------------------------------------------------
# parse_hypothesis
# ---------------------------------------------------------------------------


def test_parse_hypothesis_happy_path():
    raw = {
        "role_in_paper": "illustrates the setup",
        "overall_subject": "an interferometer",
        "expected_elements": [
            {"element_id": "exp_1", "name": "laser", "confidence": 0.6, "importance": "primary"},
        ],
        "expected_relations": [
            {"relation_id": "rel_1", "from_element_id": "exp_1", "to_element_id": "exp_1", "relation": "feeds"},
        ],
        "confidence": 0.5,
    }
    hypothesis = parse_hypothesis(raw)
    assert hypothesis.role_in_paper == "illustrates the setup"
    assert hypothesis.expected_elements[0].element_id == "exp_1"
    assert hypothesis.expected_elements[0].confidence == 0.6
    assert hypothesis.expected_relations[0].relation == "feeds"
    assert hypothesis.confidence == 0.5


def test_parse_hypothesis_handles_non_dict_input():
    hypothesis = parse_hypothesis(None)  # type: ignore[arg-type]
    assert hypothesis.role_in_paper == ""
    assert hypothesis.expected_elements == []


def test_parse_hypothesis_ignores_non_dict_list_items():
    raw = {"expected_elements": ["not-a-dict", {"element_id": "exp_1", "name": "x"}]}
    hypothesis = parse_hypothesis(raw)
    assert len(hypothesis.expected_elements) == 1
    assert hypothesis.expected_elements[0].element_id == "exp_1"


def test_parse_hypothesis_clamps_confidence_above_one():
    raw = {"confidence": 5.0, "expected_elements": [{"element_id": "exp_1", "name": "x", "confidence": 99}]}
    hypothesis = parse_hypothesis(raw)
    assert hypothesis.confidence == 1.0
    assert hypothesis.expected_elements[0].confidence == 1.0


def test_parse_hypothesis_clamps_confidence_below_zero():
    raw = {"confidence": -3.0}
    hypothesis = parse_hypothesis(raw)
    assert hypothesis.confidence == 0.0


def test_parse_hypothesis_defaults_non_numeric_confidence():
    raw = {"confidence": "not-a-number"}
    hypothesis = parse_hypothesis(raw)
    assert hypothesis.confidence == 0.0


def test_parse_hypothesis_defaults_missing_fields():
    hypothesis = parse_hypothesis({})
    assert hypothesis.role_in_paper == ""
    assert hypothesis.overall_subject == ""
    assert hypothesis.expected_visual_cues == []
    assert hypothesis.unstated_points == []
    assert hypothesis.falsification_conditions == []


# ---------------------------------------------------------------------------
# parse_observations
# ---------------------------------------------------------------------------


def test_parse_observations_happy_path():
    raw = {
        "elements": [{"observation_id": "obs_1", "kind": "box", "label_text": "L1"}],
        "connections": [{"from_observation_id": "obs_1", "to_observation_id": "obs_1", "connector": "line"}],
        "visual_mode_guess": "functional_diagram",
        "confidence": 0.4,
    }
    observations = parse_observations(raw)
    assert observations.elements[0].observation_id == "obs_1"
    assert observations.elements[0].label_text == "L1"
    assert observations.connections[0].connector == "line"
    assert observations.visual_mode_guess == "functional_diagram"
    assert observations.confidence == 0.4


def test_parse_observations_handles_non_dict_input():
    observations = parse_observations("not-a-dict")  # type: ignore[arg-type]
    assert observations.elements == []
    assert observations.visual_mode_guess == "unknown"


def test_parse_observations_ignores_non_dict_list_items():
    raw = {"elements": [123, {"observation_id": "obs_1"}]}
    observations = parse_observations(raw)
    assert len(observations.elements) == 1


def test_parse_observations_clamps_confidence():
    raw = {"confidence": 10.0}
    observations = parse_observations(raw)
    assert observations.confidence == 1.0


def test_parse_observations_keeps_out_of_vocabulary_visual_mode_guess():
    """validator.py — not this parse function — is responsible for flagging
    an out-of-vocabulary visual_mode_guess (design principle #4: never
    silently discard/coerce an unexpected LLM value)."""
    raw = {"visual_mode_guess": "totally_bogus_mode"}
    observations = parse_observations(raw)
    assert observations.visual_mode_guess == "totally_bogus_mode"


def test_parse_observations_defaults_missing_fields():
    observations = parse_observations({})
    assert observations.panels == []
    assert observations.ocr_labels == []
    assert observations.repeated_motifs == []
    assert observations.unreadable_regions == []
    assert observations.undecidable_elements == []


# ---------------------------------------------------------------------------
# parse_alignment_output
# ---------------------------------------------------------------------------


def test_parse_alignment_output_builds_record_and_iterative_parts():
    raw = {
        "apparatus_name_candidate": "Ion source assembly",
        "match_status": "novel",
        "alignment_items": [
            {"item_id": "align_1", "item_kind": "element", "label": "ion source", "status": "supported_by_both"},
        ],
        "alternative_hypotheses": [
            {"hypothesis_id": "hyp_1", "description": "alt reading", "status": "active"},
        ],
        "verification_tasks": [
            {"task_id": "task_1", "question": "is X visible?", "success_condition": "yes", "refutation_condition": "no"},
        ],
        "review_questions": [{"question_id": "q_1", "question": "is this correct?"}],
    }
    record, extra = parse_alignment_output(raw, _figure(), [])
    assert record.apparatus_name_candidate == "Ion source assembly"
    assert record.match_status == "novel"
    assert extra["alignment_items"][0].item_id == "align_1"
    assert extra["alternative_hypotheses"][0].hypothesis_id == "hyp_1"
    assert extra["verification_tasks"][0].task_id == "task_1"
    assert extra["review_questions"] == [{"question_id": "q_1", "question": "is this correct?"}]


def test_parse_alignment_output_handles_non_dict_input():
    record, extra = parse_alignment_output(None, _figure(), [])  # type: ignore[arg-type]
    assert record.match_status == "unknown"
    assert extra["alignment_items"] == []
    assert extra["alternative_hypotheses"] == []
    assert extra["verification_tasks"] == []
    assert extra["review_questions"] == []


def test_parse_alignment_output_keeps_out_of_vocabulary_status():
    raw = {"alignment_items": [{"item_id": "align_1", "status": "totally_bogus"}]}
    _, extra = parse_alignment_output(raw, _figure(), [])
    assert extra["alignment_items"][0].status == "totally_bogus"


def test_parse_alignment_output_clamps_alignment_item_confidence():
    raw = {"alignment_items": [{"item_id": "align_1", "status": "unresolved", "confidence": 42.0}]}
    _, extra = parse_alignment_output(raw, _figure(), [])
    assert extra["alignment_items"][0].confidence == 1.0


def test_parse_alignment_output_ignores_non_dict_list_items():
    raw = {"alignment_items": ["not-a-dict", {"item_id": "align_1", "status": "unresolved"}]}
    _, extra = parse_alignment_output(raw, _figure(), [])
    assert len(extra["alignment_items"]) == 1


def test_parse_alignment_output_focus_bbox_rel_defaults_to_none():
    raw = {"verification_tasks": [{"task_id": "task_1", "question": "x?"}]}
    _, extra = parse_alignment_output(raw, _figure(), [])
    assert extra["verification_tasks"][0].focus_bbox_rel is None


def test_parse_alignment_output_focus_bbox_rel_preserved_when_present():
    raw = {"verification_tasks": [{"task_id": "task_1", "question": "x?", "focus_bbox_rel": [0.1, 0.2, 0.3, 0.4]}]}
    _, extra = parse_alignment_output(raw, _figure(), [])
    assert extra["verification_tasks"][0].focus_bbox_rel == [0.1, 0.2, 0.3, 0.4]


# ---------------------------------------------------------------------------
# parse_verification_output
# ---------------------------------------------------------------------------


def test_parse_verification_output_happy_path():
    raw = {
        "task_findings": [{"task_id": "task_1", "outcome": "resolved", "observation": "seen"}],
        "updated_alignment_items": [{"item_id": "align_1", "status": "supported_by_both"}],
        "new_alignment_items": [{"item_id": "align_2", "status": "visual_only"}],
        "new_verification_tasks": [{"task_id": "task_2", "question": "is Y visible?"}],
        "hypothesis_updates": [{"hypothesis_id": "hyp_1", "status": "rejected"}],
        "record_deltas": {"parts_to_add": [{"name": "x"}]},
        "notes": "some notes",
    }
    parsed = parse_verification_output(raw)
    assert parsed["task_findings"][0]["task_id"] == "task_1"
    assert parsed["updated_alignment_items"][0].item_id == "align_1"
    assert parsed["new_alignment_items"][0].item_id == "align_2"
    assert parsed["new_verification_tasks"][0].task_id == "task_2"
    assert parsed["hypothesis_updates"] == [{"hypothesis_id": "hyp_1", "status": "rejected"}]
    assert parsed["record_deltas"] == {"parts_to_add": [{"name": "x"}]}
    assert parsed["notes"] == "some notes"


def test_parse_verification_output_handles_non_dict_input():
    parsed = parse_verification_output(None)  # type: ignore[arg-type]
    assert parsed["task_findings"] == []
    assert parsed["updated_alignment_items"] == []
    assert parsed["new_alignment_items"] == []
    assert parsed["new_verification_tasks"] == []
    assert parsed["hypothesis_updates"] == []
    assert parsed["record_deltas"] == {}
    assert parsed["notes"] == ""


def test_parse_verification_output_handles_non_dict_record_deltas():
    parsed = parse_verification_output({"record_deltas": "not-a-dict"})
    assert parsed["record_deltas"] == {}


def test_parse_verification_output_ignores_non_dict_finding_items():
    raw = {"task_findings": ["not-a-dict", {"task_id": "task_1", "outcome": "resolved"}]}
    parsed = parse_verification_output(raw)
    assert len(parsed["task_findings"]) == 1


def test_parse_verification_output_keeps_out_of_vocabulary_outcome():
    raw = {"task_findings": [{"task_id": "task_1", "outcome": "totally_bogus"}]}
    parsed = parse_verification_output(raw)
    assert parsed["task_findings"][0]["outcome"] == "totally_bogus"


def test_parse_verification_output_defaults_missing_notes_to_empty_string():
    parsed = parse_verification_output({})
    assert parsed["notes"] == ""
