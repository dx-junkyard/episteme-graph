"""Tests for the Phase 1 subtask C extension: in-figure label grounding.

Covers: FigureImageInput.inner_labels/abbreviations, ApparatusPart.label_ref/
expanded_name/bbox, the deterministic grounding in agent.py
(``_attach_label_grounding`` / ``_expand_label``), the two new validator
rules, and the prompt sections that surface the new inputs to the LLM.

All new fields/behaviour are additive — this file does not re-test anything
already covered by test_schema.py / test_validator.py / test_agent.py, and
the "source_backed is never assigned" guardrail continues to be exercised
there unchanged.
"""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.apparatus_semantics.agent import (
    ApparatusSemanticsAgent,
    _attach_label_grounding,
    _expand_label,
)
from episteme_graph.agents.apparatus_semantics.prompt import ApparatusSemanticsPromptFactory
from episteme_graph.agents.apparatus_semantics.schema import (
    ApparatusConnection,
    ApparatusPart,
    ApparatusRecord,
    ApparatusSemanticsResult,
    FigureImageInput,
)
from episteme_graph.agents.apparatus_semantics.validator import ApparatusSemanticsValidator

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"

VALIDATOR = ApparatusSemanticsValidator()


def _figure(
    figure_id: str = "fig_1",
    inner_labels=None,
    abbreviations=None,
    caption: str = "Schematic of the setup.",
    nearby=None,
    image_bytes=_PNG_BYTES,
) -> FigureImageInput:
    return FigureImageInput(
        figure_id=figure_id,
        figure_key=figure_id,
        figure_label="Figure 1",
        caption_text=caption,
        image_bytes=image_bytes,
        nearby_text=nearby if nearby is not None else [],
        inner_labels=inner_labels if inner_labels is not None else [],
        abbreviations=abbreviations if abbreviations is not None else {},
    )


def _record(**overrides) -> ApparatusRecord:
    defaults = dict(
        figure_id="fig_1",
        figure_key="fig_1",
        apparatus_name_candidate="Setup",
        matched_library_entry_id=None,
        matched_library_version_no=None,
        match_status="novel",
        parts=[],
        connections=[],
        evidence_quote="",
        reason="",
        confidence=0.5,
        source_backing_status="inferred",
        review_status="review_required",
        repair_failed=False,
    )
    defaults.update(overrides)
    return ApparatusRecord(**defaults)


def _raw_response(**overrides) -> dict:
    base = {
        "apparatus_name_candidate": "Modulator setup",
        "matched_library_entry_id": None,
        "match_status": "novel",
        "parts": [],
        "connections": [],
        "evidence_quote": "",
        "reason": "identified from image",
        "confidence": 0.7,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# schema.py: round trip + backward compatibility
# ---------------------------------------------------------------------------


def test_apparatus_part_new_fields_round_trip():
    record = _record(parts=[
        ApparatusPart(
            name="EOM",
            role="modulates phase",
            label_ref="EOM",
            expanded_name="electro-optic modulator",
            bbox=[1.0, 2.0, 3.0, 4.0],
            evidence_quote="",
            reason="",
            confidence=0.6,
        ),
    ])
    result = ApparatusSemanticsResult(document_id="doc_test", cartridge_id=None, apparatus_records=[record])
    restored = ApparatusSemanticsResult.from_dict(result.to_dict())

    part = restored.apparatus_records[0].parts[0]
    assert part.label_ref == "EOM"
    assert part.expanded_name == "electro-optic modulator"
    assert part.bbox == [1.0, 2.0, 3.0, 4.0]


def test_from_dict_defaults_when_new_part_fields_absent():
    """Older exported artifacts (pre-extension) must still round-trip (P4)."""
    minimal = {
        "document_id": "doc_test",
        "cartridge_id": None,
        "apparatus_records": [
            {
                "figure_id": "fig_x",
                "match_status": "novel",
                "parts": [{"name": "ion source", "role": "injects particles"}],
            },
        ],
        "validation_issues": [],
    }
    restored = ApparatusSemanticsResult.from_dict(minimal)
    part = restored.apparatus_records[0].parts[0]
    assert part.label_ref is None
    assert part.expanded_name == ""
    assert part.bbox is None


def test_figure_image_input_new_field_defaults():
    figure = FigureImageInput(
        figure_id="fig_1", figure_key="fig_1", figure_label=None,
        caption_text="A schematic.", image_bytes=None,
    )
    assert figure.inner_labels == []
    assert figure.abbreviations == {}


# ---------------------------------------------------------------------------
# validator.py: part_label_ref_not_in_inner_labels / inner_labels_not_covered
# ---------------------------------------------------------------------------


def test_label_ref_matching_inner_label_has_no_issue():
    figure = _figure(inner_labels=[{"text": "EOM", "bbox": [1, 2, 3, 4]}])
    record = _record(parts=[ApparatusPart(name="modulator", role="modulates", label_ref="EOM")])
    issues = VALIDATOR.validate_record(record, figure=figure)
    assert [i for i in issues if i.rule_id == "part_label_ref_not_in_inner_labels"] == []
    # fully covered -> no "not covered" warning either
    assert [i for i in issues if i.rule_id == "inner_labels_not_covered"] == []


def test_label_ref_case_insensitive_match_has_no_error():
    figure = _figure(inner_labels=[{"text": "EOM", "bbox": [1, 2, 3, 4]}])
    record = _record(parts=[ApparatusPart(name="modulator", role="modulates", label_ref="eom")])
    issues = VALIDATOR.validate_record(record, figure=figure)
    assert [i for i in issues if i.severity == "error"] == []


def test_label_ref_not_in_inner_labels_is_error():
    figure = _figure(inner_labels=[{"text": "EOM", "bbox": [1, 2, 3, 4]}])
    record = _record(parts=[ApparatusPart(name="modulator", role="modulates", label_ref="NOT_THERE")])
    issues = VALIDATOR.validate_record(record, figure=figure)
    matching = [i for i in issues if i.rule_id == "part_label_ref_not_in_inner_labels"]
    assert matching and matching[0].severity == "error"


def test_label_ref_without_any_inner_labels_is_warning_not_error():
    """A raster figure (no PDF text layer) must never error-loop repair over label_ref (P4)."""
    figure = _figure(inner_labels=[])
    record = _record(parts=[ApparatusPart(name="modulator", role="modulates", label_ref="EOM")])
    issues = VALIDATOR.validate_record(record, figure=figure)
    matching = [i for i in issues if i.rule_id == "label_ref_without_inner_labels"]
    assert matching and matching[0].severity == "warning"
    assert [i for i in issues if i.severity == "error"] == []
    # nothing to "cover" when there are no inner_labels at all
    assert [i for i in issues if i.rule_id == "inner_labels_not_covered"] == []


def test_uncovered_inner_labels_produce_single_aggregated_warning():
    figure = _figure(inner_labels=[
        {"text": "EOM", "bbox": [1, 2, 3, 4]},
        {"text": "PBS", "bbox": [5, 6, 7, 8]},
        {"text": "ECDL", "bbox": [9, 10, 11, 12]},
    ])
    record = _record(parts=[ApparatusPart(name="modulator", role="modulates", label_ref="EOM")])
    issues = VALIDATOR.validate_record(record, figure=figure)
    matching = [i for i in issues if i.rule_id == "inner_labels_not_covered"]
    assert len(matching) == 1
    assert matching[0].severity == "warning"
    assert "PBS" in matching[0].message
    assert "ECDL" in matching[0].message
    assert [i for i in issues if i.severity == "error"] == []


def test_uncovered_inner_labels_message_caps_listed_labels():
    inner_labels = [{"text": f"L{i}", "bbox": [0, 0, 1, 1]} for i in range(15)]
    figure = _figure(inner_labels=inner_labels)
    record = _record(parts=[])  # none covered
    issues = VALIDATOR.validate_record(record, figure=figure)
    matching = [i for i in issues if i.rule_id == "inner_labels_not_covered"]
    assert len(matching) == 1
    assert "15" in matching[0].message
    assert "+5 more" in matching[0].message


def test_no_figure_context_skips_label_ref_checks():
    record = _record(parts=[ApparatusPart(name="modulator", role="modulates", label_ref="EOM")])
    issues = VALIDATOR.validate_record(record, figure=None)
    assert [i for i in issues if "label_ref" in i.rule_id or "inner_labels" in i.rule_id] == []


# ---------------------------------------------------------------------------
# agent.py: _expand_label (pure function)
# ---------------------------------------------------------------------------


def test_expand_label_exact_match():
    assert _expand_label("EOM", {"EOM": "electro-optic modulator"}) == "electro-optic modulator"


def test_expand_label_case_insensitive_match():
    assert _expand_label("eom", {"EOM": "electro-optic modulator"}) == "electro-optic modulator"


def test_expand_label_prefix_slack_match():
    """'RFPDs' (plural) should still expand against the 'RFPD' abbreviation key."""
    assert _expand_label("RFPDs", {"RFPD": "radio frequency photodiode"}) == "radio frequency photodiode"


def test_expand_label_no_match_returns_empty_string():
    assert _expand_label("XYZ", {"EOM": "electro-optic modulator"}) == ""


def test_expand_label_empty_inputs_return_empty_string():
    assert _expand_label(None, {"EOM": "x"}) == ""
    assert _expand_label("EOM", {}) == ""
    assert _expand_label("EOM", None) == ""


# ---------------------------------------------------------------------------
# agent.py: _attach_label_grounding (pure function)
# ---------------------------------------------------------------------------


def test_attach_label_grounding_copies_bbox_from_matching_label():
    figure = _figure(inner_labels=[{"text": "EOM", "bbox": [10, 20, 30, 40]}])
    record = _record(parts=[ApparatusPart(name="modulator", role="modulates", label_ref="EOM")])
    _attach_label_grounding(record, figure)
    assert record.parts[0].bbox == [10, 20, 30, 40]


def test_attach_label_grounding_no_match_leaves_bbox_none():
    figure = _figure(inner_labels=[{"text": "PBS", "bbox": [10, 20, 30, 40]}])
    record = _record(parts=[ApparatusPart(name="modulator", role="modulates", label_ref="EOM")])
    _attach_label_grounding(record, figure)
    assert record.parts[0].bbox is None


def test_attach_label_grounding_sets_expanded_name_from_abbreviations():
    figure = _figure(
        inner_labels=[{"text": "EOM", "bbox": [1, 2, 3, 4]}],
        abbreviations={"EOM": "electro-optic modulator"},
    )
    record = _record(parts=[ApparatusPart(name="EOM", role="modulates", label_ref="EOM")])
    _attach_label_grounding(record, figure)
    assert record.parts[0].expanded_name == "electro-optic modulator"


def test_attach_label_grounding_is_noop_on_empty_parts():
    figure = _figure(inner_labels=[{"text": "EOM", "bbox": [1, 2, 3, 4]}])
    record = _record(parts=[])
    _attach_label_grounding(record, figure)  # must not raise
    assert record.parts == []


def test_attach_label_grounding_ignores_llm_supplied_bbox_and_expanded_name():
    """bbox/expanded_name are always overwritten deterministically, never trusted verbatim."""
    figure = _figure(inner_labels=[{"text": "EOM", "bbox": [10, 20, 30, 40]}])
    record = _record(parts=[
        ApparatusPart(
            name="modulator",
            role="modulates",
            label_ref="EOM",
            bbox=[999, 999, 999, 999],
            expanded_name="totally made up",
        ),
    ])
    _attach_label_grounding(record, figure)
    assert record.parts[0].bbox == [10, 20, 30, 40]
    assert record.parts[0].expanded_name == ""  # no abbreviations supplied -> ""


# ---------------------------------------------------------------------------
# agent.py: end-to-end via ApparatusSemanticsAgent.run() with a fake LLM
# ---------------------------------------------------------------------------


def test_run_attaches_bbox_from_label_ref():
    agent = ApparatusSemanticsAgent()
    figure = _figure(inner_labels=[{"text": "EOM", "bbox": [10, 20, 30, 40]}])
    response = _raw_response(parts=[
        {"name": "modulator", "role": "modulates phase", "label_ref": "EOM", "confidence": 0.7},
    ])

    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})

    part = result.apparatus_records[0].parts[0]
    assert part.label_ref == "EOM"
    assert part.bbox == [10, 20, 30, 40]


def test_run_ignores_llm_supplied_bbox_and_expanded_name():
    agent = ApparatusSemanticsAgent()
    figure = _figure(inner_labels=[{"text": "EOM", "bbox": [10, 20, 30, 40]}])
    response = _raw_response(parts=[
        {
            "name": "modulator",
            "role": "modulates phase",
            "label_ref": "EOM",
            # An LLM should never be able to inject these - repair._parse_record
            # does not even read them, but assert end-to-end regardless.
            "bbox": [999, 999, 999, 999],
            "expanded_name": "hallucinated expansion",
            "confidence": 0.7,
        },
    ])

    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})

    part = result.apparatus_records[0].parts[0]
    assert part.bbox == [10, 20, 30, 40]
    # no abbreviations supplied for this figure -> deterministic lookup yields
    # "", never the LLM-supplied "hallucinated expansion"
    assert part.expanded_name == ""


def test_run_expands_abbreviation_with_prefix_slack():
    agent = ApparatusSemanticsAgent()
    figure = _figure(
        inner_labels=[{"text": "RFPDs", "bbox": [1, 2, 3, 4]}],
        abbreviations={"RFPD": "radio frequency photodiode"},
    )
    response = _raw_response(parts=[
        {"name": "RFPDs", "role": "detects RF signal", "label_ref": "RFPDs", "confidence": 0.7},
    ])

    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})

    part = result.apparatus_records[0].parts[0]
    assert part.expanded_name == "radio frequency photodiode"


def test_run_repairs_invalid_label_ref_then_grounds_corrected_output():
    agent = ApparatusSemanticsAgent()
    figure = _figure(inner_labels=[{"text": "EOM", "bbox": [10, 20, 30, 40]}])
    bad = _raw_response(parts=[
        {"name": "modulator", "role": "modulates phase", "label_ref": "NOT_A_REAL_LABEL", "confidence": 0.7},
    ])
    fixed = _raw_response(parts=[
        {"name": "modulator", "role": "modulates phase", "label_ref": "EOM", "confidence": 0.7},
    ])

    with patch.object(agent._llm_client, "generate", side_effect=[bad, fixed]) as mock_llm:
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})

    assert mock_llm.call_count == 2
    part = result.apparatus_records[0].parts[0]
    assert part.label_ref == "EOM"
    assert part.bbox == [10, 20, 30, 40]


def test_run_image_unavailable_fallback_has_no_parts_and_no_grounding_crash():
    agent = ApparatusSemanticsAgent()
    figure = _figure(image_bytes=None, inner_labels=[{"text": "EOM", "bbox": [1, 2, 3, 4]}])
    with patch.object(agent._llm_client, "generate") as mock_llm:
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})
    mock_llm.assert_not_called()
    assert result.apparatus_records[0].parts == []


def test_source_backed_never_assigned_with_label_grounding_in_play():
    """Extends the existing package-wide guardrail to the new label fields."""
    agent = ApparatusSemanticsAgent()
    figure = _figure(
        inner_labels=[{"text": "EOM", "bbox": [1, 2, 3, 4]}],
        abbreviations={"EOM": "electro-optic modulator"},
    )
    response = _raw_response(parts=[
        {"name": "modulator", "role": "modulates phase", "label_ref": "EOM", "confidence": 0.7},
    ])
    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})

    record = result.apparatus_records[0]
    assert record.source_backing_status != "source_backed"
    assert record.review_status == "review_required"


# ---------------------------------------------------------------------------
# prompt.py: inner_label_hints / abbreviations surfaced in user content
# ---------------------------------------------------------------------------


def test_build_messages_includes_inner_label_hints_and_abbreviations():
    factory = ApparatusSemanticsPromptFactory()
    figure = _figure()
    messages = factory.build_messages(
        figure, [], [], {},
        inner_label_hints=["EOM", "PBS"],
        abbreviations={"EOM": "electro-optic modulator"},
    )
    content = messages[1]["content"]
    assert "In-Figure Labels" in content
    assert "EOM" in content
    assert "PBS" in content
    assert "Abbreviation Dictionary" in content
    assert "electro-optic modulator" in content


def test_build_messages_omits_sections_when_hints_absent():
    factory = ApparatusSemanticsPromptFactory()
    figure = _figure()
    messages = factory.build_messages(figure, [], [], {})
    content = messages[1]["content"]
    assert "In-Figure Labels" not in content
    assert "Abbreviation Dictionary" not in content


def test_build_repair_messages_also_includes_hints():
    factory = ApparatusSemanticsPromptFactory()
    figure = _figure()
    messages = factory.build_repair_messages(
        figure, [], [], {}, {"match_status": "novel"}, [],
        inner_label_hints=["EOM"], abbreviations={"EOM": "electro-optic modulator"},
    )
    content = messages[1]["content"]
    assert "EOM" in content
    assert "electro-optic modulator" in content


def test_output_schema_mentions_label_ref():
    factory = ApparatusSemanticsPromptFactory()
    figure = _figure()
    messages = factory.build_messages(figure, [], [], {})
    assert "label_ref" in messages[1]["content"]


def test_cartridge_relation_types_override_default_vocabulary():
    factory = ApparatusSemanticsPromptFactory()
    figure = _figure()
    messages = factory.build_messages(figure, [], [], {"relation_types": ["custom_relation"]})
    content = messages[1]["content"]
    assert "custom_relation" in content
    assert "optical_path" not in content


def test_default_relation_vocabulary_used_without_cartridge_hint():
    factory = ApparatusSemanticsPromptFactory()
    figure = _figure()
    messages = factory.build_messages(figure, [], [], {})
    content = messages[1]["content"]
    assert "optical_path" in content
