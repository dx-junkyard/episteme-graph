"""Tests for ApparatusSemantics schema helpers."""
import json

from episteme_graph.agents.apparatus_semantics.schema import (
    MATCH_STATUSES,
    REVIEW_STATUS_DEFAULT,
    REVIEW_STATUSES,
    SOURCE_BACKING_STATUSES,
    ApparatusConnection,
    ApparatusPart,
    ApparatusRecord,
    ApparatusSemanticsResult,
    FigureImageInput,
    LibraryCandidate,
    ValidationIssue,
)


def _make_record(figure_id: str = "fig_1") -> ApparatusRecord:
    return ApparatusRecord(
        figure_id=figure_id,
        figure_key=figure_id,
        apparatus_name_candidate="Time-of-flight drift chamber",
        matched_library_entry_id="lib_entry_0001",
        matched_library_version_no=3,
        match_status="matched",
        parts=[
            ApparatusPart(
                name="ion source",
                role="injects charged particles",
                evidence_quote="an ion source that injects charged particles",
                reason="named in nearby text",
                confidence=0.88,
            ),
        ],
        connections=[
            ApparatusConnection(
                from_part="ion source",
                to_part="drift tube",
                relation="injects particles into",
                reason="described in caption",
                confidence=0.8,
            ),
        ],
        evidence_quote="Schematic of the vacuum chamber assembly",
        reason="matches retrieved candidate",
        confidence=0.86,
        source_backing_status="partially_source_backed",
        review_status=REVIEW_STATUS_DEFAULT,
        repair_failed=False,
    )


# ------------------------------------------------------------------
# Controlled vocabularies
# ------------------------------------------------------------------

def test_match_statuses_vocabulary():
    assert MATCH_STATUSES == ("matched", "novel", "unknown")


def test_source_backing_statuses_vocabulary():
    assert SOURCE_BACKING_STATUSES == (
        "source_backed",
        "partially_source_backed",
        "inferred",
        "review_required",
    )


def test_review_status_is_always_review_required_family():
    assert REVIEW_STATUS_DEFAULT == "review_required"
    assert REVIEW_STATUSES == ("review_required",)


# ------------------------------------------------------------------
# Round-trip serialization
# ------------------------------------------------------------------

def test_to_json_round_trip():
    record = _make_record()
    result = ApparatusSemanticsResult(
        document_id="doc_test",
        cartridge_id="particle_physics",
        apparatus_records=[record],
    )
    restored = ApparatusSemanticsResult.from_dict(json.loads(result.to_json()))

    assert restored.document_id == "doc_test"
    assert restored.cartridge_id == "particle_physics"
    rec = restored.apparatus_records[0]
    assert rec.figure_id == "fig_1"
    assert rec.match_status == "matched"
    assert rec.matched_library_entry_id == "lib_entry_0001"
    assert rec.matched_library_version_no == 3
    assert rec.parts[0].name == "ion source"
    assert rec.parts[0].confidence == 0.88
    assert rec.connections[0].from_part == "ion source"
    assert rec.connections[0].to_part == "drift tube"
    assert rec.source_backing_status == "partially_source_backed"
    assert rec.review_status == "review_required"
    assert rec.repair_failed is False


def test_to_dict_is_json_serializable():
    result = ApparatusSemanticsResult(
        document_id="doc_test",
        cartridge_id=None,
        apparatus_records=[_make_record()],
        validation_issues=[ValidationIssue("some_rule", "warning", "message text")],
    )
    # Must not raise — every field in the result graph is JSON-native.
    payload = json.dumps(result.to_dict())
    assert "ion source" in payload


def test_from_dict_defaults_for_minimal_record():
    minimal = {
        "document_id": "doc_test",
        "cartridge_id": None,
        "apparatus_records": [
            {"figure_id": "fig_x", "match_status": "unknown"},
        ],
        "validation_issues": [],
    }
    restored = ApparatusSemanticsResult.from_dict(minimal)
    rec = restored.apparatus_records[0]
    assert rec.figure_key == "fig_x"  # falls back to figure_id
    assert rec.apparatus_name_candidate == ""
    assert rec.matched_library_entry_id is None
    assert rec.parts == []
    assert rec.connections == []
    assert rec.source_backing_status == "inferred"
    assert rec.review_status == "review_required"
    assert rec.repair_failed is False


def test_empty_result_round_trip():
    result = ApparatusSemanticsResult(document_id="doc_empty", cartridge_id=None)
    restored = ApparatusSemanticsResult.from_dict(json.loads(result.to_json()))
    assert restored.apparatus_records == []
    assert restored.validation_issues == []


# ------------------------------------------------------------------
# Input dataclasses (defaults / construction)
# ------------------------------------------------------------------

def test_figure_image_input_defaults():
    figure = FigureImageInput(
        figure_id="fig_1",
        figure_key="fig_1",
        figure_label="Figure 1",
        caption_text="A schematic.",
        image_bytes=None,
    )
    assert figure.nearby_text == []
    assert figure.figure_record is None


def test_library_candidate_defaults():
    candidate = LibraryCandidate(entry_id="lib_0001", version_no=1, name="Widget")
    assert candidate.aliases == []
    assert candidate.summary == ""
    assert candidate.body == {}


# ------------------------------------------------------------------
# Guided re-analysis fields (guided_figure_reanalysis_design.md §6-1)
# ------------------------------------------------------------------


def test_figure_image_input_guidance_field_defaults():
    """Batch-pipeline figures never set these — all default falsy (GF7)."""
    figure = FigureImageInput(
        figure_id="fig_1",
        figure_key="fig_1",
        figure_label="Figure 1",
        caption_text="A schematic.",
        image_bytes=None,
    )
    assert figure.guidance_text == ""
    assert figure.focus_bbox_rel is None
    assert figure.focus_image_bytes is None
    assert figure.focus_label_texts == []


def test_figure_image_input_guidance_fields_can_be_set():
    figure = FigureImageInput(
        figure_id="fig_1",
        figure_key="fig_1",
        figure_label="Figure 1",
        caption_text="A schematic.",
        image_bytes=b"\x89PNG\r\n\x1a\nimg",
        guidance_text="look at the left box",
        focus_bbox_rel=[0.1, 0.2, 0.5, 0.6],
        focus_image_bytes=b"\xff\xd8\xffcrop",
        focus_label_texts=["EOM"],
    )
    assert figure.guidance_text == "look at the left box"
    assert figure.focus_bbox_rel == [0.1, 0.2, 0.5, 0.6]
    assert figure.focus_image_bytes == b"\xff\xd8\xffcrop"
    assert figure.focus_label_texts == ["EOM"]


def test_apparatus_record_guidance_note_defaults_to_empty_string():
    record = _make_record()
    assert record.guidance_note == ""


def test_guidance_note_round_trips_through_to_dict_and_from_dict():
    record = _make_record()
    record.guidance_note = "Found the EOM in the focus region as requested."
    result = ApparatusSemanticsResult(
        document_id="doc_test", cartridge_id=None, apparatus_records=[record],
    )
    restored = ApparatusSemanticsResult.from_dict(json.loads(result.to_json()))
    assert restored.apparatus_records[0].guidance_note == (
        "Found the EOM in the focus region as requested."
    )


def test_guidance_note_defaults_to_empty_string_when_absent_from_dict():
    """Artifacts exported before this extension must still round-trip (P4)."""
    minimal = {
        "document_id": "doc_test",
        "cartridge_id": None,
        "apparatus_records": [
            {"figure_id": "fig_x", "match_status": "unknown"},
        ],
        "validation_issues": [],
    }
    restored = ApparatusSemanticsResult.from_dict(minimal)
    assert restored.apparatus_records[0].guidance_note == ""
