from episteme_graph.agents.figure_modes import (
    analysis_profile_for_record,
    effective_mode,
    functional_profile_from_apparatus,
    infer_mode_from_text,
)
from episteme_graph.agents.apparatus_semantics.agent import (
    _attach_label_grounding,
    _attach_profile_grounding,
)
from episteme_graph.agents.apparatus_semantics.llm_client import APPARATUS_RESPONSE_SCHEMA
from episteme_graph.agents.apparatus_semantics.repair import _parse_record
from episteme_graph.agents.apparatus_semantics.schema import FigureImageInput
from episteme_graph.agents.apparatus_semantics.schema import (
    ApparatusRecord,
    ApparatusSemanticsResult,
)
from episteme_graph.agents.apparatus_semantics.validator import ApparatusSemanticsValidator
from episteme_graph.agents.component_assembly.apparatus_components import build_apparatus_components


def test_caption_fallback_is_conservative_and_can_identify_basic_modes():
    assert infer_mode_from_text("Measured intensity versus time")[0] == "data_plot"
    assert infer_mode_from_text("Photograph of the specimen")[0] == "descriptive_image"
    assert infer_mode_from_text("Schematic of the optical setup")[0] == "functional_diagram"
    assert infer_mode_from_text("Figure without a caption")[0] == "unknown"


def test_teacher_reviewed_mode_wins_over_suggestion():
    assert effective_mode("data_plot", "functional_diagram") == "functional_diagram"
    assert effective_mode("data_plot", None) == "data_plot"


def test_legacy_apparatus_is_adapted_to_function_port_connection_contract():
    profile = functional_profile_from_apparatus({
        "apparatus_name_candidate": "Detector chain",
        "parts": [
            {"name": "sensor", "role": "detects"},
            {"name": "amplifier", "role": "amplifies"},
        ],
        "connections": [
            {"from_part": "sensor", "to_part": "amplifier", "relation": "signal"},
        ],
    })
    connection = profile["connections"][0]
    assert profile["overall_function"] == "Detector chain"
    assert connection["from_function_id"] == "function_1"
    assert connection["to_function_id"] == "function_2"
    assert connection["from_output_id"]
    assert connection["to_input_id"]
    assert profile["functions"][0]["outputs"][0]["id"] == connection["from_output_id"]
    assert profile["functions"][1]["inputs"][0]["id"] == connection["to_input_id"]


def test_data_plot_keeps_axes_observations_and_interpretations_separate():
    normalized = analysis_profile_for_record({
        "suggested_mode": "data_plot",
        "analysis_profile": {
            "axes": [{"orientation": "x", "name": "time", "unit": "s"}],
            "observations": [{"id": "o1", "description": "a peak is visible"}],
            "interpretations": [{"observation_id": "o1", "meaning_candidate": "resonance"}],
        },
    })
    profile = normalized["analysis_profile"]
    assert profile["axes"][0]["orientation"] == "x"
    assert profile["observations"][0]["id"] == "o1"
    assert profile["interpretations"][0]["observation_id"] == "o1"


def test_data_plot_assigns_stable_local_ids_when_observations_omit_them():
    normalized = analysis_profile_for_record({
        "suggested_mode": "data_plot",
        "analysis_profile": {
            "observations": [{"description": "a peak is visible"}],
            "highlights": [{"label": "peak region"}],
        },
    })
    profile = normalized["analysis_profile"]
    assert profile["observations"][0]["id"] == "observation_1"
    assert profile["highlights"][0]["id"] == "highlight_1"


def test_explicit_unknown_is_not_reclassified_from_legacy_looking_fields():
    normalized = analysis_profile_for_record({
        "suggested_mode": "unknown",
        "mode_reason": "insufficient visual evidence",
        "parts": [{"name": "shape"}],
    })
    assert normalized["suggested_mode"] == "unknown"


def test_vision_response_schema_routes_all_specialist_profiles():
    properties = APPARATUS_RESPONSE_SCHEMA["properties"]
    assert properties["suggested_mode"]["enum"] == [
        "functional_diagram", "data_plot", "descriptive_image", "mixed", "unknown"
    ]
    profile = properties["analysis_profile"]["properties"]
    for key in ("functions", "connections", "axes", "observations", "interpretations", "subjects"):
        assert key in profile


def test_data_plot_parse_does_not_become_an_apparatus_and_keeps_profile():
    figure = FigureImageInput(
        figure_id="f1", figure_key="fig_1", figure_label="Figure 1",
        caption_text="Signal versus time", image_bytes=b"image",
    )
    record = _parse_record({
        "suggested_mode": "data_plot",
        "mode_reason": "visible axes",
        "analysis_profile": {
            "axes": [{"orientation": "x", "name": "time"}],
            "observations": [{"id": "o1", "description": "peak"}],
            "interpretations": [{"observation_id": "o1", "meaning_candidate": "event"}],
        },
        "apparatus_name_candidate": "",
        "matched_library_entry_id": None,
        "match_status": "unknown",
        "parts": [],
        "connections": [],
    }, figure, [])
    assert record.suggested_mode == "data_plot"
    assert record.parts == []
    assert record.analysis_profile["interpretations"][0]["observation_id"] == "o1"
    assert not [
        issue for issue in ApparatusSemanticsValidator().validate_record(record, figure=figure)
        if issue.severity == "error"
    ]


def test_profile_bbox_is_attached_from_pdf_label_not_model_bbox():
    figure = FigureImageInput(
        figure_id="f1", figure_key="fig_1", figure_label="Figure 1",
        caption_text="Schematic", image_bytes=b"image",
        inner_labels=[{"text": "PD", "bbox": [10, 20, 30, 40]}],
    )
    record = _parse_record({
        "suggested_mode": "functional_diagram",
        "analysis_profile": {
            "functions": [{
                "id": "detector", "name": "Detector", "label_ref": "PD",
                "bbox": [999, 999, 999, 999], "inputs": [], "outputs": [],
            }],
            "connections": [],
        },
        "apparatus_name_candidate": "Detector",
        "match_status": "novel",
        "parts": [{"name": "Detector", "label_ref": "PD", "confidence": 0.5}],
        "connections": [],
    }, figure, [])
    _attach_label_grounding(record, figure)
    _attach_profile_grounding(record, figure)
    assert record.analysis_profile["functions"][0]["bbox"] == [10, 20, 30, 40]


def test_functional_connection_requires_all_function_and_port_ids():
    figure = FigureImageInput(
        figure_id="f1", figure_key="fig_1", figure_label=None,
        caption_text="Schematic", image_bytes=b"image",
    )
    record = _parse_record({
        "suggested_mode": "functional_diagram",
        "analysis_profile": {
            "functions": [
                {"id": "a", "name": "A", "inputs": [], "outputs": []},
                {"id": "b", "name": "B", "inputs": [], "outputs": []},
            ],
            "connections": [{"from_function_id": "a", "to_function_id": "b"}],
        },
        "apparatus_name_candidate": "system",
        "match_status": "novel",
        "parts": [],
        "connections": [],
    }, figure, [])
    issues = ApparatusSemanticsValidator().validate_record(record, figure=figure)
    assert any(issue.rule_id == "functional_connection_missing_endpoint_id" for issue in issues)


def test_nonfunctional_and_new_unknown_records_do_not_create_apparatus_components():
    common = dict(
        figure_key="fig_1",
        apparatus_name_candidate="",
        matched_library_entry_id=None,
        matched_library_version_no=None,
        match_status="unknown",
    )
    records = [
        ApparatusRecord(
            figure_id="plot", suggested_mode="data_plot", mode_reason="visible axes", **common
        ),
        ApparatusRecord(
            figure_id="photo", suggested_mode="descriptive_image", mode_reason="photo", **common
        ),
        ApparatusRecord(
            figure_id="unclear", suggested_mode="unknown", mode_reason="insufficient", **common
        ),
    ]
    result = ApparatusSemanticsResult(
        document_id="doc", cartridge_id=None, apparatus_records=records
    )
    assert build_apparatus_components(result, "doc") == []
