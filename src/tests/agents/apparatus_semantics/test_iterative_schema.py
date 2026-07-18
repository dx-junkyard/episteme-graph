"""Tests for the Wave 1 iterative contextual-analysis schema additions.

Design doc: docs/features/contextual_figure_analysis_iterative_verification.md.

Covers: to_dict/from_dict round-trips for every new dataclass (via
``ApparatusRecord.iterative_analysis`` — the only place these are reachable
from the existing JSON round-trip path), permissive ``from_dict`` behaviour
(unknown keys ignored, missing keys default), backward compatibility of old
artifacts (no ``iterative_analysis`` key at all), and the new controlled
vocabularies.

This file is additive — it does not modify or re-test anything already
covered by test_schema.py.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from episteme_graph.agents.apparatus_semantics.schema import (
    ALIGNMENT_SEVERITIES,
    ALIGNMENT_STATUSES,
    CONVERGENCE_STATUSES,
    HYPOTHESIS_STATUSES,
    VERIFICATION_TASK_STATUSES,
    AlignmentItem,
    AlternativeHypothesis,
    ApparatusRecord,
    ApparatusSemanticsResult,
    ContextHypothesis,
    ExpectedElement,
    ExpectedRelation,
    IterativeAnalysisRecord,
    IterativeConfig,
    ObservedConnection,
    ObservedElement,
    VerificationIterationRecord,
    VerificationTask,
    VisualObservationSet,
)


def _minimal_record(figure_id: str = "fig_1") -> ApparatusRecord:
    return ApparatusRecord(
        figure_id=figure_id,
        figure_key=figure_id,
        apparatus_name_candidate="",
        matched_library_entry_id=None,
        matched_library_version_no=None,
        match_status="unknown",
    )


def _full_iterative_analysis() -> IterativeAnalysisRecord:
    hypothesis = ContextHypothesis(
        role_in_paper="illustrates the optical setup",
        overall_subject="a laser interferometer",
        expected_elements=[
            ExpectedElement(
                element_id="exp_1",
                name="laser source",
                evidence_quote="a laser source emits",
                expected_labels=["L1"],
                importance="primary",
                confidence=0.7,
            ),
        ],
        expected_relations=[
            ExpectedRelation(
                relation_id="rel_1",
                from_element_id="exp_1",
                to_element_id="exp_1",
                relation="feeds",
                direction="from->to",
                evidence_quote="",
                confidence=0.5,
            ),
        ],
        expected_visual_cues=["a box labeled L1"],
        unstated_points=["exact wavelength"],
        falsification_conditions=["no box appears at all"],
        evidence_quotes=["a laser source emits"],
        reason="text clearly describes the setup",
        confidence=0.6,
    )
    observations = VisualObservationSet(
        panels=[{"panel_id": "a", "description": "left panel", "region_hint": "left"}],
        elements=[
            ObservedElement(
                observation_id="obs_1",
                kind="box",
                description="a rectangular box",
                label_text="L1",
                region_hint="upper-left",
            ),
        ],
        connections=[
            ObservedConnection(
                from_observation_id="obs_1",
                to_observation_id="obs_1",
                connector="line",
                direction="from->to",
                description="connects to itself for test purposes",
            ),
        ],
        ocr_labels=["L1"],
        repeated_motifs=[{"description": "boxes", "observation_ids": ["obs_1"]}],
        unreadable_regions=[{"region_hint": "bottom", "reason": "low resolution"}],
        undecidable_elements=[{"observation_id": "obs_1", "note": "ambiguous shape"}],
        visual_mode_guess="functional_diagram",
        reason="clear panel layout",
        confidence=0.8,
    )
    alignment_items = [
        AlignmentItem(
            item_id="align_1",
            item_kind="element",
            label="laser source",
            status="supported_by_both",
            expected_ref="exp_1",
            observation_refs=["obs_1"],
            label_ref="L1",
            text_evidence="a laser source emits",
            visual_evidence="a rectangular box labeled L1",
            severity="low",
            reason="both sources agree",
            confidence=0.9,
        ),
    ]
    alternative_hypotheses = [
        AlternativeHypothesis(
            hypothesis_id="hyp_1",
            description="alternative reading: a beam splitter setup",
            supporting_evidence=["shape resembles a splitter"],
            counter_evidence=["caption never mentions splitting"],
            unverified_conditions=["need to confirm second output arm"],
            status="active",
            confidence=0.3,
        ),
    ]
    verification_iterations = [
        VerificationIterationRecord(
            iteration_index=1,
            executed_task_ids=["task_1"],
            findings=[{"task_id": "task_1", "outcome": "resolved", "observation": "seen"}],
            changes=["confirmed L1 is a laser source"],
            vision_call_used=True,
            notes="first pass",
        ),
    ]
    open_verification_tasks = [
        VerificationTask(
            task_id="task_2",
            question="is there a second output arm?",
            target_item_ids=["align_1"],
            region_hint="right side",
            focus_bbox_rel=[0.5, 0.0, 1.0, 1.0],
            success_condition="a second arm is visible",
            refutation_condition="no second arm is visible",
            status="open",
        ),
    ]
    return IterativeAnalysisRecord(
        enabled=True,
        context_hypothesis=hypothesis,
        visual_observations=observations,
        alignment_items=alignment_items,
        alternative_hypotheses=alternative_hypotheses,
        verification_iterations=verification_iterations,
        open_verification_tasks=open_verification_tasks,
        unresolved_conflicts=[{"item_id": "align_1", "label": "laser source"}],
        review_questions=[{"question_id": "q_1", "question": "is this a splitter?"}],
        convergence_status="max_iterations_reached",
        stage_failures=["verification: vision call timed out"],
        llm_calls=4,
        vision_calls=3,
        model="gpt-test",
    )


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------


def test_alignment_statuses_vocabulary():
    assert ALIGNMENT_STATUSES == (
        "supported_by_both", "visual_only", "text_only", "contradicted", "unresolved",
    )


def test_convergence_statuses_vocabulary():
    assert CONVERGENCE_STATUSES == (
        "converged", "max_iterations_reached", "no_progress", "aborted_error",
        "aborted_cost_limit", "not_run",
    )


def test_hypothesis_statuses_vocabulary():
    assert HYPOTHESIS_STATUSES == ("active", "rejected", "selected")


def test_verification_task_statuses_vocabulary():
    assert VERIFICATION_TASK_STATUSES == ("open", "resolved", "refuted", "unresolved")


def test_alignment_severities_vocabulary():
    assert ALIGNMENT_SEVERITIES == ("high", "medium", "low")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_iterative_config_defaults():
    config = IterativeConfig()
    assert config.enabled is False
    assert config.max_iterations == 3
    assert config.vision_call_budget is None
    assert config.model_name == ""


def test_iterative_config_round_trip_via_asdict():
    config = IterativeConfig(enabled=True, max_iterations=5, vision_call_budget=10, model_name="m1")
    restored = IterativeConfig(**asdict(config))
    assert restored == config


def test_apparatus_record_iterative_analysis_defaults_to_none():
    record = _minimal_record()
    assert record.iterative_analysis is None


def test_expected_element_defaults():
    element = ExpectedElement(element_id="exp_1", name="thing")
    assert element.evidence_quote == ""
    assert element.expected_labels == []
    assert element.importance == "primary"
    assert element.confidence == 0.0


def test_expected_relation_defaults():
    relation = ExpectedRelation(relation_id="rel_1", from_element_id="a", to_element_id="b", relation="feeds")
    assert relation.direction == ""
    assert relation.evidence_quote == ""
    assert relation.confidence == 0.0


def test_context_hypothesis_defaults():
    hypothesis = ContextHypothesis()
    assert hypothesis.role_in_paper == ""
    assert hypothesis.expected_elements == []
    assert hypothesis.expected_relations == []
    assert hypothesis.confidence == 0.0


def test_observed_element_defaults():
    element = ObservedElement(observation_id="obs_1")
    assert element.kind == "other"
    assert element.description == ""
    assert element.label_text == ""
    assert element.region_hint == ""


def test_observed_connection_defaults():
    connection = ObservedConnection(from_observation_id="obs_1", to_observation_id="obs_2")
    assert connection.connector == ""
    assert connection.direction == ""
    assert connection.description == ""


def test_visual_observation_set_defaults():
    observations = VisualObservationSet()
    assert observations.elements == []
    assert observations.connections == []
    assert observations.visual_mode_guess == "unknown"
    assert observations.confidence == 0.0


def test_alignment_item_defaults():
    item = AlignmentItem(item_id="align_1", item_kind="element", label="thing", status="unresolved")
    assert item.expected_ref == ""
    assert item.observation_refs == []
    assert item.label_ref == ""
    assert item.severity == "medium"
    assert item.confidence == 0.0


def test_alternative_hypothesis_defaults():
    hypothesis = AlternativeHypothesis(hypothesis_id="hyp_1", description="an alternative reading")
    assert hypothesis.supporting_evidence == []
    assert hypothesis.counter_evidence == []
    assert hypothesis.unverified_conditions == []
    assert hypothesis.status == "active"
    assert hypothesis.confidence == 0.0


def test_verification_task_defaults():
    task = VerificationTask(task_id="task_1", question="is X visible?")
    assert task.target_item_ids == []
    assert task.region_hint == ""
    assert task.focus_bbox_rel is None
    assert task.success_condition == ""
    assert task.refutation_condition == ""
    assert task.status == "open"


def test_verification_iteration_record_defaults():
    iteration = VerificationIterationRecord(iteration_index=1)
    assert iteration.executed_task_ids == []
    assert iteration.findings == []
    assert iteration.changes == []
    assert iteration.vision_call_used is True
    assert iteration.notes == ""


def test_iterative_analysis_record_defaults():
    record = IterativeAnalysisRecord()
    assert record.enabled is True
    assert record.context_hypothesis is None
    assert record.visual_observations is None
    assert record.alignment_items == []
    assert record.alternative_hypotheses == []
    assert record.verification_iterations == []
    assert record.open_verification_tasks == []
    assert record.unresolved_conflicts == []
    assert record.review_questions == []
    assert record.convergence_status == "not_run"
    assert record.stage_failures == []
    assert record.llm_calls == 0
    assert record.vision_calls == 0
    assert record.model == ""


# ---------------------------------------------------------------------------
# Round-trip serialization (via ApparatusRecord.iterative_analysis, the only
# place these dataclasses are reachable from the existing to_json()/
# from_dict() path)
# ---------------------------------------------------------------------------


def test_full_iterative_analysis_round_trips_through_to_json_and_from_dict():
    record = _minimal_record()
    record.iterative_analysis = _full_iterative_analysis()
    result = ApparatusSemanticsResult(
        document_id="doc_test", cartridge_id=None, apparatus_records=[record],
    )

    restored = ApparatusSemanticsResult.from_dict(json.loads(result.to_json()))
    ia = restored.apparatus_records[0].iterative_analysis

    assert ia is not None
    assert ia.enabled is True
    assert ia.convergence_status == "max_iterations_reached"
    assert ia.stage_failures == ["verification: vision call timed out"]
    assert ia.llm_calls == 4
    assert ia.vision_calls == 3
    assert ia.model == "gpt-test"

    # ContextHypothesis + nested ExpectedElement/ExpectedRelation
    assert ia.context_hypothesis is not None
    assert ia.context_hypothesis.role_in_paper == "illustrates the optical setup"
    assert ia.context_hypothesis.expected_elements[0].element_id == "exp_1"
    assert ia.context_hypothesis.expected_elements[0].name == "laser source"
    assert ia.context_hypothesis.expected_elements[0].confidence == 0.7
    assert ia.context_hypothesis.expected_relations[0].relation_id == "rel_1"
    assert ia.context_hypothesis.expected_relations[0].relation == "feeds"

    # VisualObservationSet + nested ObservedElement/ObservedConnection
    assert ia.visual_observations is not None
    assert ia.visual_observations.visual_mode_guess == "functional_diagram"
    assert ia.visual_observations.elements[0].observation_id == "obs_1"
    assert ia.visual_observations.elements[0].label_text == "L1"
    assert ia.visual_observations.connections[0].connector == "line"

    # AlignmentItem
    assert ia.alignment_items[0].item_id == "align_1"
    assert ia.alignment_items[0].status == "supported_by_both"
    assert ia.alignment_items[0].observation_refs == ["obs_1"]

    # AlternativeHypothesis
    assert ia.alternative_hypotheses[0].hypothesis_id == "hyp_1"
    assert ia.alternative_hypotheses[0].status == "active"

    # VerificationIterationRecord
    assert ia.verification_iterations[0].iteration_index == 1
    assert ia.verification_iterations[0].executed_task_ids == ["task_1"]

    # VerificationTask (open_verification_tasks)
    assert ia.open_verification_tasks[0].task_id == "task_2"
    assert ia.open_verification_tasks[0].focus_bbox_rel == [0.5, 0.0, 1.0, 1.0]

    assert ia.unresolved_conflicts == [{"item_id": "align_1", "label": "laser source"}]
    assert ia.review_questions == [{"question_id": "q_1", "question": "is this a splitter?"}]


def test_iterative_analysis_serializes_to_json_native_dict():
    record = _minimal_record()
    record.iterative_analysis = _full_iterative_analysis()
    result = ApparatusSemanticsResult(
        document_id="doc_test", cartridge_id=None, apparatus_records=[record],
    )
    # Must not raise — every field in the result graph (including the new
    # iterative_analysis subtree) is JSON-native.
    payload = json.dumps(result.to_dict())
    assert "laser source" in payload


# ---------------------------------------------------------------------------
# Backward compatibility (P4 — artifacts exported before this extension)
# ---------------------------------------------------------------------------


def test_apparatus_record_from_dict_defaults_iterative_analysis_to_none_when_absent():
    minimal = {
        "document_id": "doc_test",
        "cartridge_id": None,
        "apparatus_records": [
            {"figure_id": "fig_x", "match_status": "unknown"},
        ],
        "validation_issues": [],
    }
    restored = ApparatusSemanticsResult.from_dict(minimal)
    assert restored.apparatus_records[0].iterative_analysis is None


def test_apparatus_record_from_dict_treats_non_dict_iterative_analysis_as_none():
    minimal = {
        "document_id": "doc_test",
        "cartridge_id": None,
        "apparatus_records": [
            {"figure_id": "fig_x", "match_status": "unknown", "iterative_analysis": "not-a-dict"},
        ],
        "validation_issues": [],
    }
    restored = ApparatusSemanticsResult.from_dict(minimal)
    assert restored.apparatus_records[0].iterative_analysis is None


# ---------------------------------------------------------------------------
# Permissive from_dict: unknown keys ignored, missing keys default
# ---------------------------------------------------------------------------


def test_iterative_analysis_from_dict_is_permissive_to_unknown_and_missing_keys():
    minimal = {
        "document_id": "doc_test",
        "cartridge_id": None,
        "apparatus_records": [
            {
                "figure_id": "fig_x",
                "match_status": "unknown",
                "iterative_analysis": {
                    "convergence_status": "converged",
                    "some_unexpected_future_field": {"nested": True},
                    "context_hypothesis": {
                        "role_in_paper": "x",
                        "unexpected_field_on_hypothesis": 123,
                        "expected_elements": [
                            {"element_id": "exp_1", "unexpected_field": "z"},
                        ],
                    },
                    "visual_observations": {
                        "elements": [{"observation_id": "obs_1", "unexpected_field": "z"}],
                    },
                    "alignment_items": [
                        {"item_id": "align_1", "status": "unresolved", "unexpected_field": "z"},
                    ],
                    "verification_iterations": [
                        {"iteration_index": 1, "unexpected_field": "z"},
                    ],
                    "open_verification_tasks": [
                        {"task_id": "task_1", "unexpected_field": "z"},
                    ],
                },
            },
        ],
        "validation_issues": [],
    }
    restored = ApparatusSemanticsResult.from_dict(minimal)
    ia = restored.apparatus_records[0].iterative_analysis

    assert ia is not None
    assert ia.convergence_status == "converged"
    # Missing keys fall back to dataclass defaults.
    assert ia.enabled is True
    assert ia.llm_calls == 0
    assert ia.model == ""

    assert ia.context_hypothesis.role_in_paper == "x"
    assert ia.context_hypothesis.expected_elements[0].element_id == "exp_1"
    # Missing field on a nested item defaults rather than raising.
    assert ia.context_hypothesis.expected_elements[0].name == ""

    assert ia.visual_observations.elements[0].observation_id == "obs_1"
    assert ia.visual_observations.visual_mode_guess == "unknown"

    assert ia.alignment_items[0].item_id == "align_1"
    assert ia.alignment_items[0].status == "unresolved"

    assert ia.verification_iterations[0].iteration_index == 1
    assert ia.verification_iterations[0].executed_task_ids == []

    assert ia.open_verification_tasks[0].task_id == "task_1"
    assert ia.open_verification_tasks[0].status == "open"


def test_iterative_analysis_from_dict_handles_completely_empty_dict():
    minimal = {
        "document_id": "doc_test",
        "cartridge_id": None,
        "apparatus_records": [
            {"figure_id": "fig_x", "match_status": "unknown", "iterative_analysis": {}},
        ],
        "validation_issues": [],
    }
    restored = ApparatusSemanticsResult.from_dict(minimal)
    ia = restored.apparatus_records[0].iterative_analysis
    assert ia is not None
    assert ia.context_hypothesis is None
    assert ia.visual_observations is None
    assert ia.alignment_items == []
    assert ia.convergence_status == "not_run"
