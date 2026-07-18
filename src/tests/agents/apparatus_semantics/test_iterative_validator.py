"""Tests for the Wave 1 iterative-pipeline validation methods on
``ApparatusSemanticsValidator``.

Design doc: docs/features/contextual_figure_analysis_iterative_verification.md.

One rule_id per test, mirroring the existing test_validator.py style. This
file is additive — it does not modify or re-test ``validate_record``/
``validate`` (already covered by test_validator.py).
"""
from __future__ import annotations

from episteme_graph.agents.apparatus_semantics.schema import (
    REVIEW_STATUS_DEFAULT,
    AlignmentItem,
    AlternativeHypothesis,
    ApparatusConnection,
    ApparatusPart,
    ApparatusRecord,
    ContextHypothesis,
    ExpectedElement,
    ExpectedRelation,
    IterativeAnalysisRecord,
    ObservedConnection,
    ObservedElement,
    VerificationIterationRecord,
    VerificationTask,
    VisualObservationSet,
)
from episteme_graph.agents.apparatus_semantics.validator import ApparatusSemanticsValidator

VALIDATOR = ApparatusSemanticsValidator()


def _record(**overrides) -> ApparatusRecord:
    defaults = dict(
        figure_id="fig_1",
        figure_key="fig_1",
        apparatus_name_candidate="Drift chamber",
        matched_library_entry_id=None,
        matched_library_version_no=None,
        match_status="novel",
        parts=[],
        connections=[],
        evidence_quote="",
        reason="",
        confidence=0.7,
        source_backing_status="inferred",
        review_status=REVIEW_STATUS_DEFAULT,
        repair_failed=False,
    )
    defaults.update(overrides)
    return ApparatusRecord(**defaults)


def _figure_stub(caption="A schematic of the ion source.", nearby=None, inner_labels=None):
    from episteme_graph.agents.apparatus_semantics.schema import FigureImageInput
    return FigureImageInput(
        figure_id="fig_1",
        figure_key="fig_1",
        figure_label="Figure 1",
        caption_text=caption,
        image_bytes=b"\x89PNG\r\n\x1a\nrest",
        nearby_text=nearby or [],
        inner_labels=inner_labels or [],
    )


# ---------------------------------------------------------------------------
# validate_hypothesis
# ---------------------------------------------------------------------------


def test_validate_hypothesis_happy_path_has_no_errors():
    hypothesis = ContextHypothesis(
        expected_elements=[ExpectedElement(element_id="exp_1", name="ion source", confidence=0.5)],
        expected_relations=[
            ExpectedRelation(relation_id="rel_1", from_element_id="exp_1", to_element_id="exp_1", relation="feeds"),
        ],
        confidence=0.5,
    )
    issues = VALIDATOR.validate_hypothesis(hypothesis)
    assert [i for i in issues if i.severity == "error"] == []


def test_hypothesis_confidence_out_of_range_is_error():
    hypothesis = ContextHypothesis(confidence=1.5)
    issues = VALIDATOR.validate_hypothesis(hypothesis)
    assert any(i.rule_id == "hypothesis_confidence_out_of_range" and i.severity == "error" for i in issues)


def test_expected_element_confidence_out_of_range_is_error():
    hypothesis = ContextHypothesis(
        expected_elements=[ExpectedElement(element_id="exp_1", name="x", confidence=-0.5)],
    )
    issues = VALIDATOR.validate_hypothesis(hypothesis)
    assert any(i.rule_id == "expected_element_confidence_out_of_range" for i in issues)


def test_expected_element_missing_id_is_error():
    hypothesis = ContextHypothesis(
        expected_elements=[ExpectedElement(element_id="", name="x")],
    )
    issues = VALIDATOR.validate_hypothesis(hypothesis)
    assert any(i.rule_id == "expected_element_missing_id" and i.severity == "error" for i in issues)


def test_expected_element_duplicate_id_is_error():
    hypothesis = ContextHypothesis(
        expected_elements=[
            ExpectedElement(element_id="exp_1", name="x"),
            ExpectedElement(element_id="exp_1", name="y"),
        ],
    )
    issues = VALIDATOR.validate_hypothesis(hypothesis)
    assert any(i.rule_id == "expected_element_duplicate_id" and i.severity == "error" for i in issues)


def test_expected_element_evidence_not_verbatim_is_warning():
    hypothesis = ContextHypothesis(
        expected_elements=[
            ExpectedElement(element_id="exp_1", name="x", evidence_quote="totally fabricated quote"),
        ],
    )
    issues = VALIDATOR.validate_hypothesis(hypothesis, figure=_figure_stub())
    matching = [i for i in issues if i.rule_id == "expected_element_evidence_not_verbatim"]
    assert matching and matching[0].severity == "warning"


def test_expected_element_evidence_verbatim_has_no_warning():
    hypothesis = ContextHypothesis(
        expected_elements=[
            ExpectedElement(element_id="exp_1", name="x", evidence_quote="ion source"),
        ],
    )
    issues = VALIDATOR.validate_hypothesis(hypothesis, figure=_figure_stub())
    assert not any(i.rule_id == "expected_element_evidence_not_verbatim" for i in issues)


def test_expected_relation_confidence_out_of_range_is_error():
    hypothesis = ContextHypothesis(
        expected_elements=[ExpectedElement(element_id="exp_1", name="x")],
        expected_relations=[
            ExpectedRelation(
                relation_id="rel_1", from_element_id="exp_1", to_element_id="exp_1",
                relation="feeds", confidence=2.0,
            ),
        ],
    )
    issues = VALIDATOR.validate_hypothesis(hypothesis)
    assert any(i.rule_id == "expected_relation_confidence_out_of_range" for i in issues)


def test_expected_relation_unknown_element_is_warning():
    hypothesis = ContextHypothesis(
        expected_elements=[ExpectedElement(element_id="exp_1", name="x")],
        expected_relations=[
            ExpectedRelation(
                relation_id="rel_1", from_element_id="exp_1", to_element_id="exp_missing",
                relation="feeds",
            ),
        ],
    )
    issues = VALIDATOR.validate_hypothesis(hypothesis)
    matching = [i for i in issues if i.rule_id == "expected_relation_unknown_element"]
    assert matching and matching[0].severity == "warning"


# ---------------------------------------------------------------------------
# validate_observations
# ---------------------------------------------------------------------------


def test_validate_observations_happy_path_has_no_errors():
    observations = VisualObservationSet(
        elements=[ObservedElement(observation_id="obs_1")],
        connections=[ObservedConnection(from_observation_id="obs_1", to_observation_id="obs_1")],
        visual_mode_guess="functional_diagram",
        confidence=0.5,
    )
    issues = VALIDATOR.validate_observations(observations)
    assert [i for i in issues if i.severity == "error"] == []


def test_observations_confidence_out_of_range_is_error():
    observations = VisualObservationSet(confidence=1.2)
    issues = VALIDATOR.validate_observations(observations)
    assert any(i.rule_id == "observations_confidence_out_of_range" for i in issues)


def test_invalid_visual_mode_guess_is_error():
    observations = VisualObservationSet(visual_mode_guess="totally_bogus_mode")
    issues = VALIDATOR.validate_observations(observations)
    assert any(i.rule_id == "invalid_visual_mode_guess" and i.severity == "error" for i in issues)


def test_observed_element_missing_id_is_error():
    observations = VisualObservationSet(elements=[ObservedElement(observation_id="")])
    issues = VALIDATOR.validate_observations(observations)
    assert any(i.rule_id == "observed_element_missing_id" and i.severity == "error" for i in issues)


def test_observed_element_duplicate_id_is_error():
    observations = VisualObservationSet(
        elements=[ObservedElement(observation_id="obs_1"), ObservedElement(observation_id="obs_1")],
    )
    issues = VALIDATOR.validate_observations(observations)
    assert any(i.rule_id == "observed_element_duplicate_id" for i in issues)


def test_observed_connection_unknown_observation_is_warning():
    observations = VisualObservationSet(
        elements=[ObservedElement(observation_id="obs_1")],
        connections=[ObservedConnection(from_observation_id="obs_1", to_observation_id="obs_missing")],
    )
    issues = VALIDATOR.validate_observations(observations)
    matching = [i for i in issues if i.rule_id == "observed_connection_unknown_observation"]
    assert matching and matching[0].severity == "warning"


# ---------------------------------------------------------------------------
# validate_alignment
# ---------------------------------------------------------------------------


def test_validate_alignment_happy_path_has_no_errors():
    record = _record(
        parts=[ApparatusPart(name="ion source", role="injects particles")],
    )
    iterative_parts = {
        "alignment_items": [
            AlignmentItem(
                item_id="align_1", item_kind="element", label="ion source",
                status="supported_by_both", label_ref="ION", text_evidence="injects particles",
                visual_evidence="a labeled box", severity="low",
            ),
        ],
        "alternative_hypotheses": [],
        "verification_tasks": [],
    }
    figure = _figure_stub(
        caption="injects particles", inner_labels=[{"text": "ION", "bbox": [0, 0, 1, 1]}],
    )
    issues = VALIDATOR.validate_alignment(record, iterative_parts, figure=figure)
    assert [i for i in issues if i.severity == "error"] == []


def test_invalid_alignment_status_is_error():
    record = _record()
    iterative_parts = {"alignment_items": [
        AlignmentItem(item_id="align_1", item_kind="element", label="x", status="totally_bogus"),
    ]}
    issues = VALIDATOR.validate_alignment(record, iterative_parts)
    assert any(i.rule_id == "invalid_alignment_status" and i.severity == "error" for i in issues)


def test_invalid_alignment_severity_is_warning():
    record = _record()
    iterative_parts = {"alignment_items": [
        AlignmentItem(
            item_id="align_1", item_kind="element", label="x", status="unresolved",
            severity="extreme",
        ),
    ]}
    issues = VALIDATOR.validate_alignment(record, iterative_parts)
    matching = [i for i in issues if i.rule_id == "invalid_alignment_severity"]
    assert matching and matching[0].severity == "warning"


def test_text_only_with_visual_refs_is_error():
    record = _record()
    iterative_parts = {"alignment_items": [
        AlignmentItem(
            item_id="align_1", item_kind="element", label="x", status="text_only",
            observation_refs=["obs_1"],
        ),
    ]}
    issues = VALIDATOR.validate_alignment(record, iterative_parts)
    assert any(i.rule_id == "text_only_with_visual_refs" and i.severity == "error" for i in issues)


def test_text_only_with_label_ref_is_error():
    record = _record()
    iterative_parts = {"alignment_items": [
        AlignmentItem(
            item_id="align_1", item_kind="element", label="x", status="text_only",
            label_ref="L1", text_evidence="something",
        ),
    ]}
    issues = VALIDATOR.validate_alignment(record, iterative_parts)
    assert any(i.rule_id == "text_only_with_visual_refs" and i.severity == "error" for i in issues)


def test_alignment_missing_text_evidence_is_error_for_text_only():
    record = _record()
    iterative_parts = {"alignment_items": [
        AlignmentItem(item_id="align_1", item_kind="element", label="x", status="text_only"),
    ]}
    issues = VALIDATOR.validate_alignment(record, iterative_parts)
    assert any(i.rule_id == "alignment_missing_text_evidence" and i.severity == "error" for i in issues)


def test_alignment_missing_visual_evidence_is_error_for_visual_only():
    record = _record()
    iterative_parts = {"alignment_items": [
        AlignmentItem(item_id="align_1", item_kind="element", label="x", status="visual_only"),
    ]}
    issues = VALIDATOR.validate_alignment(record, iterative_parts)
    assert any(i.rule_id == "alignment_missing_visual_evidence" and i.severity == "error" for i in issues)


def test_alignment_unknown_observation_ref_is_warning():
    record = _record()
    iterative_parts = {"alignment_items": [
        AlignmentItem(
            item_id="align_1", item_kind="element", label="x", status="visual_only",
            observation_refs=["obs_ghost"],
        ),
    ]}
    observations = VisualObservationSet(elements=[ObservedElement(observation_id="obs_1")])
    issues = VALIDATOR.validate_alignment(record, iterative_parts, observations=observations)
    matching = [i for i in issues if i.rule_id == "alignment_unknown_observation_ref"]
    assert matching and matching[0].severity == "warning"


def test_alignment_label_ref_not_in_inner_labels_is_error():
    record = _record()
    iterative_parts = {"alignment_items": [
        AlignmentItem(
            item_id="align_1", item_kind="element", label="x", status="visual_only",
            label_ref="GHOST",
        ),
    ]}
    figure = _figure_stub(inner_labels=[{"text": "ION", "bbox": [0, 0, 1, 1]}])
    issues = VALIDATOR.validate_alignment(record, iterative_parts, figure=figure)
    assert any(i.rule_id == "alignment_label_ref_not_in_inner_labels" and i.severity == "error" for i in issues)


def test_alignment_text_evidence_not_verbatim_is_warning():
    record = _record()
    iterative_parts = {"alignment_items": [
        AlignmentItem(
            item_id="align_1", item_kind="element", label="x", status="text_only",
            text_evidence="a completely fabricated sentence",
        ),
    ]}
    figure = _figure_stub(caption="the real caption text")
    issues = VALIDATOR.validate_alignment(record, iterative_parts, figure=figure)
    matching = [i for i in issues if i.rule_id == "alignment_text_evidence_not_verbatim"]
    assert matching and matching[0].severity == "warning"


def test_part_without_visual_support_is_error_when_no_matching_item():
    record = _record(parts=[ApparatusPart(name="ion source", role="injects particles")])
    issues = VALIDATOR.validate_alignment(record, {"alignment_items": []})
    assert any(i.rule_id == "part_without_visual_support" and i.severity == "error" for i in issues)


def test_part_without_visual_support_is_error_when_item_is_text_only():
    record = _record(parts=[ApparatusPart(name="ion source", role="injects particles")])
    iterative_parts = {"alignment_items": [
        AlignmentItem(
            item_id="align_1", item_kind="element", label="ion source", status="text_only",
            text_evidence="injects particles",
        ),
    ]}
    issues = VALIDATOR.validate_alignment(record, iterative_parts)
    assert any(i.rule_id == "part_without_visual_support" and i.severity == "error" for i in issues)


def test_part_with_visual_support_by_label_ref_has_no_error():
    record = _record(parts=[ApparatusPart(name="ion source", role="x", label_ref="ION")])
    iterative_parts = {"alignment_items": [
        AlignmentItem(
            item_id="align_1", item_kind="element", label="something else",
            status="supported_by_both", label_ref="ION", text_evidence="x", visual_evidence="y",
        ),
    ]}
    issues = VALIDATOR.validate_alignment(record, iterative_parts)
    assert not any(i.rule_id == "part_without_visual_support" for i in issues)


def test_part_with_visual_support_by_name_has_no_error():
    record = _record(parts=[ApparatusPart(name="Ion Source", role="x")])
    iterative_parts = {"alignment_items": [
        AlignmentItem(
            item_id="align_1", item_kind="element", label="ion source",
            status="visual_only", visual_evidence="a box",
        ),
    ]}
    issues = VALIDATOR.validate_alignment(record, iterative_parts)
    assert not any(i.rule_id == "part_without_visual_support" for i in issues)


def test_verification_task_incomplete_is_warning():
    record = _record()
    iterative_parts = {"verification_tasks": [
        VerificationTask(task_id="task_1", question="is X visible?"),
    ]}
    issues = VALIDATOR.validate_alignment(record, iterative_parts)
    matching = [i for i in issues if i.rule_id == "verification_task_incomplete"]
    assert matching and matching[0].severity == "warning"


def test_verification_task_complete_has_no_warning():
    record = _record()
    iterative_parts = {"verification_tasks": [
        VerificationTask(
            task_id="task_1", question="is X visible?",
            success_condition="X is visible", refutation_condition="X is not visible",
        ),
    ]}
    issues = VALIDATOR.validate_alignment(record, iterative_parts)
    assert not any(i.rule_id == "verification_task_incomplete" for i in issues)


def test_invalid_hypothesis_status_is_error():
    record = _record()
    iterative_parts = {"alternative_hypotheses": [
        AlternativeHypothesis(hypothesis_id="hyp_1", description="x", status="maybe"),
    ]}
    issues = VALIDATOR.validate_alignment(record, iterative_parts)
    assert any(i.rule_id == "invalid_hypothesis_status" and i.severity == "error" for i in issues)


def test_validate_alignment_still_runs_validate_record_checks():
    """validate_alignment must not bypass the existing validate_record rules
    (e.g. the hard source_backed guardrail)."""
    record = _record(source_backing_status="source_backed")
    issues = VALIDATOR.validate_alignment(record, {})
    assert any(i.rule_id == "apparatus_must_not_be_source_backed" for i in issues)


# ---------------------------------------------------------------------------
# validate_verification_output
# ---------------------------------------------------------------------------


def test_validate_verification_output_happy_path_has_no_errors():
    parsed = {
        "task_findings": [{"task_id": "task_1", "outcome": "resolved"}],
        "record_deltas": {"parts_to_add": [{"name": "x", "observation_refs": ["obs_1"]}]},
    }
    issues = VALIDATOR.validate_verification_output(
        parsed, known_task_ids={"task_1"},
        observations=VisualObservationSet(elements=[ObservedElement(observation_id="obs_1")]),
    )
    assert [i for i in issues if i.severity == "error"] == []


def test_verification_unknown_task_is_warning():
    parsed = {"task_findings": [{"task_id": "task_ghost", "outcome": "resolved"}]}
    issues = VALIDATOR.validate_verification_output(parsed, known_task_ids={"task_1"})
    matching = [i for i in issues if i.rule_id == "verification_unknown_task"]
    assert matching and matching[0].severity == "warning"


def test_invalid_verification_outcome_is_error():
    parsed = {"task_findings": [{"task_id": "task_1", "outcome": "maybe"}]}
    issues = VALIDATOR.validate_verification_output(parsed)
    assert any(i.rule_id == "invalid_verification_outcome" and i.severity == "error" for i in issues)


def test_delta_part_without_visual_support_is_error():
    parsed = {"record_deltas": {"parts_to_add": [{"name": "ghost part"}]}}
    issues = VALIDATOR.validate_verification_output(parsed)
    assert any(i.rule_id == "delta_part_without_visual_support" and i.severity == "error" for i in issues)


def test_delta_part_with_observation_ref_has_no_error_when_unverifiable():
    """When no ``observations``/``figure`` context is supplied, the check
    degrades to trusting presence of a ref rather than raising false errors."""
    parsed = {"record_deltas": {"parts_to_add": [{"name": "x", "observation_refs": ["obs_1"]}]}}
    issues = VALIDATOR.validate_verification_output(parsed)
    assert not any(i.rule_id == "delta_part_without_visual_support" for i in issues)


def test_delta_part_with_unknown_observation_ref_is_error_when_verifiable():
    parsed = {"record_deltas": {"parts_to_add": [{"name": "x", "observation_refs": ["obs_ghost"]}]}}
    observations = VisualObservationSet(elements=[ObservedElement(observation_id="obs_1")])
    issues = VALIDATOR.validate_verification_output(parsed, observations=observations)
    assert any(i.rule_id == "delta_part_without_visual_support" for i in issues)


def test_delta_part_with_label_ref_has_no_error():
    parsed = {"record_deltas": {"parts_to_add": [{"name": "x", "label_ref": "ION"}]}}
    figure = _figure_stub(inner_labels=[{"text": "ION", "bbox": [0, 0, 1, 1]}])
    issues = VALIDATOR.validate_verification_output(parsed, figure=figure)
    assert not any(i.rule_id == "delta_part_without_visual_support" for i in issues)


# ---------------------------------------------------------------------------
# validate_iterative_record
# ---------------------------------------------------------------------------


def test_validate_iterative_record_happy_path_has_no_errors():
    ia = IterativeAnalysisRecord(
        convergence_status="converged",
        verification_iterations=[
            VerificationIterationRecord(iteration_index=1, executed_task_ids=["task_1"]),
        ],
    )
    issues = VALIDATOR.validate_iterative_record(ia)
    assert [i for i in issues if i.severity == "error"] == []


def test_invalid_convergence_status_is_error():
    ia = IterativeAnalysisRecord(convergence_status="totally_bogus")
    issues = VALIDATOR.validate_iterative_record(ia)
    assert any(i.rule_id == "invalid_convergence_status" and i.severity == "error" for i in issues)


def test_nonconverged_without_review_questions_is_warning():
    ia = IterativeAnalysisRecord(
        convergence_status="max_iterations_reached", review_questions=[], unresolved_conflicts=[],
    )
    issues = VALIDATOR.validate_iterative_record(ia)
    matching = [i for i in issues if i.rule_id == "nonconverged_without_review_questions"]
    assert matching and matching[0].severity == "warning"


def test_nonconverged_with_review_questions_has_no_warning():
    ia = IterativeAnalysisRecord(
        convergence_status="max_iterations_reached",
        review_questions=[{"question_id": "q_1", "question": "is this a splitter?"}],
    )
    issues = VALIDATOR.validate_iterative_record(ia)
    assert not any(i.rule_id == "nonconverged_without_review_questions" for i in issues)


def test_converged_without_review_questions_has_no_warning():
    ia = IterativeAnalysisRecord(convergence_status="converged")
    issues = VALIDATOR.validate_iterative_record(ia)
    assert not any(i.rule_id == "nonconverged_without_review_questions" for i in issues)


def test_iteration_without_tasks_is_error():
    ia = IterativeAnalysisRecord(
        convergence_status="converged",
        verification_iterations=[VerificationIterationRecord(iteration_index=1, executed_task_ids=[])],
    )
    issues = VALIDATOR.validate_iterative_record(ia)
    assert any(i.rule_id == "iteration_without_tasks" and i.severity == "error" for i in issues)
