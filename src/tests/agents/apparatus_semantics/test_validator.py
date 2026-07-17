"""Tests for ApparatusSemanticsValidator."""
from episteme_graph.agents.apparatus_semantics.schema import (
    REVIEW_STATUS_DEFAULT,
    ApparatusConnection,
    ApparatusPart,
    ApparatusRecord,
    ApparatusSemanticsResult,
    FigureImageInput,
)
from episteme_graph.agents.apparatus_semantics.validator import ApparatusSemanticsValidator

VALIDATOR = ApparatusSemanticsValidator()


def _figure(caption: str = "A schematic of the drift chamber apparatus.", nearby=None) -> FigureImageInput:
    return FigureImageInput(
        figure_id="fig_1",
        figure_key="fig_1",
        figure_label="Figure 1",
        caption_text=caption,
        image_bytes=b"\x89PNG\r\n\x1a\nrest",
        nearby_text=nearby or ["The ion source injects particles into the tube."],
    )


def _record(**overrides) -> ApparatusRecord:
    defaults = dict(
        figure_id="fig_1",
        figure_key="fig_1",
        apparatus_name_candidate="Drift chamber",
        matched_library_entry_id=None,
        matched_library_version_no=None,
        match_status="novel",
        parts=[ApparatusPart(name="ion source", role="injects particles", confidence=0.7)],
        connections=[],
        evidence_quote="drift chamber apparatus",
        reason="matches visual cues",
        confidence=0.7,
        source_backing_status="partially_source_backed",
        review_status=REVIEW_STATUS_DEFAULT,
        repair_failed=False,
    )
    defaults.update(overrides)
    return ApparatusRecord(**defaults)


# ------------------------------------------------------------------
# Happy path
# ------------------------------------------------------------------

def test_valid_record_has_no_errors():
    record = _record()
    issues = VALIDATOR.validate_record(record, figure=_figure(), candidate_ids=set())
    assert [i for i in issues if i.severity == "error"] == []


# ------------------------------------------------------------------
# Controlled vocabulary
# ------------------------------------------------------------------

def test_invalid_match_status_is_error():
    record = _record(match_status="probably_maybe")
    issues = VALIDATOR.validate_record(record)
    assert any(i.rule_id == "invalid_match_status" and i.severity == "error" for i in issues)


def test_source_backed_is_hard_error():
    """Guardrail: source_backing_status must never be 'source_backed' (design principle #2/#4)."""
    record = _record(source_backing_status="source_backed")
    issues = VALIDATOR.validate_record(record)
    matching = [i for i in issues if i.rule_id == "apparatus_must_not_be_source_backed"]
    assert matching and matching[0].severity == "error"


def test_invalid_source_backing_status_is_error():
    record = _record(source_backing_status="totally_confirmed")
    issues = VALIDATOR.validate_record(record)
    assert any(i.rule_id == "invalid_source_backing_status" for i in issues)


def test_invalid_review_status_is_error():
    record = _record(review_status="teacher_approved")
    issues = VALIDATOR.validate_record(record)
    assert any(i.rule_id == "invalid_review_status" and i.severity == "error" for i in issues)


# ------------------------------------------------------------------
# Confidence ranges
# ------------------------------------------------------------------

def test_confidence_out_of_range_is_error():
    record = _record(confidence=1.5)
    issues = VALIDATOR.validate_record(record)
    assert any(i.rule_id == "confidence_out_of_range" for i in issues)


def test_part_confidence_out_of_range_is_error():
    record = _record(parts=[ApparatusPart(name="x", role="y", confidence=-0.1)])
    issues = VALIDATOR.validate_record(record)
    assert any(i.rule_id == "part_confidence_out_of_range" for i in issues)


def test_connection_confidence_out_of_range_is_error():
    record = _record(
        parts=[ApparatusPart(name="a", role="r1"), ApparatusPart(name="b", role="r2")],
        connections=[ApparatusConnection(from_part="a", to_part="b", relation="feeds", confidence=2.0)],
    )
    issues = VALIDATOR.validate_record(record)
    assert any(i.rule_id == "connection_confidence_out_of_range" for i in issues)


# ------------------------------------------------------------------
# matched_library_entry_id consistency
# ------------------------------------------------------------------

def test_matched_without_entry_id_is_error():
    record = _record(match_status="matched", matched_library_entry_id=None)
    issues = VALIDATOR.validate_record(record, candidate_ids={"lib_1"})
    assert any(i.rule_id == "matched_without_entry_id" for i in issues)


def test_matched_entry_id_not_in_candidates_is_error():
    record = _record(match_status="matched", matched_library_entry_id="lib_unknown")
    issues = VALIDATOR.validate_record(record, candidate_ids={"lib_1", "lib_2"})
    assert any(i.rule_id == "matched_entry_id_not_in_candidates" for i in issues)


def test_matched_entry_id_in_candidates_is_valid():
    record = _record(match_status="matched", matched_library_entry_id="lib_1")
    issues = VALIDATOR.validate_record(record, candidate_ids={"lib_1", "lib_2"})
    assert [i for i in issues if i.severity == "error"] == []


def test_novel_with_entry_id_is_warning_not_error():
    record = _record(match_status="novel", matched_library_entry_id="lib_1")
    issues = VALIDATOR.validate_record(record, candidate_ids={"lib_1"})
    matching = [i for i in issues if i.rule_id == "unexpected_matched_entry_id"]
    assert matching and matching[0].severity == "warning"
    assert [i for i in issues if i.severity == "error"] == []


# ------------------------------------------------------------------
# Required fields
# ------------------------------------------------------------------

def test_empty_apparatus_name_candidate_is_error_unless_unknown():
    record = _record(match_status="novel", apparatus_name_candidate="")
    issues = VALIDATOR.validate_record(record)
    assert any(i.rule_id == "empty_apparatus_name_candidate" for i in issues)

    unknown_record = _record(match_status="unknown", apparatus_name_candidate="")
    issues_unknown = VALIDATOR.validate_record(unknown_record)
    assert [i for i in issues_unknown if i.rule_id == "empty_apparatus_name_candidate"] == []


def test_part_missing_name_is_error():
    record = _record(parts=[ApparatusPart(name="", role="something")])
    issues = VALIDATOR.validate_record(record)
    assert any(i.rule_id == "part_missing_name" for i in issues)


def test_connection_referencing_unknown_part_is_warning():
    record = _record(
        parts=[ApparatusPart(name="a", role="r1")],
        connections=[ApparatusConnection(from_part="a", to_part="ghost", relation="feeds")],
    )
    issues = VALIDATOR.validate_record(record)
    matching = [i for i in issues if i.rule_id == "connection_references_unknown_part"]
    assert matching and matching[0].severity == "warning"


# ------------------------------------------------------------------
# Evidence-based verbatim check
# ------------------------------------------------------------------

def test_evidence_quote_verbatim_in_source_has_no_warning():
    figure = _figure(caption="A schematic of the drift chamber apparatus.")
    record = _record(evidence_quote="drift chamber apparatus")
    issues = VALIDATOR.validate_record(record, figure=figure)
    assert [i for i in issues if i.rule_id == "evidence_quote_not_verbatim"] == []


def test_evidence_quote_not_in_source_is_warning():
    figure = _figure(caption="A schematic of the drift chamber apparatus.")
    record = _record(evidence_quote="this text does not appear anywhere")
    issues = VALIDATOR.validate_record(record, figure=figure)
    matching = [i for i in issues if i.rule_id == "evidence_quote_not_verbatim"]
    assert matching and matching[0].severity == "warning"


def test_part_evidence_quote_not_in_source_is_warning():
    figure = _figure(caption="A schematic of the drift chamber apparatus.", nearby=["ion source detail"])
    record = _record(parts=[
        ApparatusPart(name="x", role="y", evidence_quote="fabricated quote not present anywhere"),
    ])
    issues = VALIDATOR.validate_record(record, figure=figure)
    assert any(i.rule_id == "part_evidence_quote_not_verbatim" for i in issues)


def test_no_figure_context_skips_evidence_check():
    record = _record(evidence_quote="anything at all, unverifiable without figure context")
    issues = VALIDATOR.validate_record(record, figure=None)
    assert [i for i in issues if "evidence_quote_not_verbatim" in i.rule_id] == []


# ------------------------------------------------------------------
# Aggregate validate() over a full result
# ------------------------------------------------------------------

def test_duplicate_library_entry_match_is_info():
    rec1 = _record(figure_id="fig_1", figure_key="fig_1", match_status="matched", matched_library_entry_id="lib_1")
    rec2 = _record(figure_id="fig_2", figure_key="fig_2", match_status="matched", matched_library_entry_id="lib_1")
    result = ApparatusSemanticsResult(document_id="doc_test", cartridge_id=None, apparatus_records=[rec1, rec2])
    issues = VALIDATOR.validate(result)
    matching = [i for i in issues if i.rule_id == "duplicate_library_entry_match"]
    assert matching and matching[0].severity == "info"


def test_validate_aggregates_all_records():
    good = _record(figure_id="fig_1", figure_key="fig_1")
    bad = _record(figure_id="fig_2", figure_key="fig_2", match_status="nonsense")
    result = ApparatusSemanticsResult(document_id="doc_test", cartridge_id=None, apparatus_records=[good, bad])
    issues = VALIDATOR.validate(result)
    assert any(i.rule_id == "invalid_match_status" for i in issues)


# ------------------------------------------------------------------
# Guided re-analysis: guidance_note_missing / guidance_note_unexpected
# (guided_figure_reanalysis_design.md §6-5) — both must be warnings, never
# errors, so they can never trigger the repair loop or block delivery.
# ------------------------------------------------------------------


def test_guidance_note_missing_is_warning_when_guidance_given_but_note_empty():
    figure = _figure()
    figure.guidance_text = "look at the left component"
    record = _record(guidance_note="")
    issues = VALIDATOR.validate_record(record, figure=figure)
    matching = [i for i in issues if i.rule_id == "guidance_note_missing"]
    assert matching and matching[0].severity == "warning"


def test_guidance_note_missing_via_focus_bbox_without_hint_text():
    figure = _figure()
    figure.focus_bbox_rel = [0.1, 0.2, 0.5, 0.6]
    record = _record(guidance_note="")
    issues = VALIDATOR.validate_record(record, figure=figure)
    assert any(i.rule_id == "guidance_note_missing" for i in issues)


def test_no_guidance_note_missing_warning_when_guidance_note_present():
    figure = _figure()
    figure.guidance_text = "look at the left component"
    record = _record(guidance_note="Found it in the focus region.")
    issues = VALIDATOR.validate_record(record, figure=figure)
    assert [i for i in issues if i.rule_id == "guidance_note_missing"] == []


def test_guidance_note_unexpected_is_warning_when_no_guidance_input():
    figure = _figure()  # no guidance_text / focus_bbox_rel / focus_image_bytes
    record = _record(guidance_note="I focused on the left box as instructed.")
    issues = VALIDATOR.validate_record(record, figure=figure)
    matching = [i for i in issues if i.rule_id == "guidance_note_unexpected"]
    assert matching and matching[0].severity == "warning"


def test_no_guidance_note_unexpected_warning_for_unguided_empty_note():
    figure = _figure()
    record = _record(guidance_note="")
    issues = VALIDATOR.validate_record(record, figure=figure)
    assert [i for i in issues if i.rule_id == "guidance_note_unexpected"] == []


def test_guidance_note_checks_never_produce_errors():
    figure = _figure()
    figure.guidance_text = "look at the left component"
    # Missing note, given guidance -> only a warning, never an error.
    record = _record(guidance_note="")
    issues = VALIDATOR.validate_record(record, figure=figure)
    guidance_issues = [i for i in issues if i.rule_id.startswith("guidance_note_")]
    assert guidance_issues
    assert all(i.severity == "warning" for i in guidance_issues)


def test_guidance_checks_skipped_without_figure_context():
    record = _record(guidance_note="anything")
    issues = VALIDATOR.validate_record(record, figure=None)
    assert [i for i in issues if i.rule_id.startswith("guidance_note_")] == []
