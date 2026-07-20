"""Regression tests for findings from the apparatus-diagram implementation review."""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.apparatus_semantics.agent import ApparatusSemanticsAgent
from episteme_graph.agents.apparatus_semantics.llm_client import APPARATUS_RESPONSE_SCHEMA
from episteme_graph.agents.apparatus_semantics.schema import FigureImageInput


def test_contextual_label_warning_is_preserved_in_final_result():
    figure = FigureImageInput(
        figure_id="fig_1",
        figure_key="fig_1",
        figure_label="Figure 1",
        caption_text="Schematic of the setup.",
        image_bytes=b"\x89PNG\r\n\x1a\nimage",
        inner_labels=[
            {"text": "EOM", "bbox": [1, 2, 3, 4]},
            {"text": "PBS", "bbox": [5, 6, 7, 8]},
        ],
    )
    response = {
        "apparatus_name_candidate": "Optical setup",
        "matched_library_entry_id": None,
        "match_status": "novel",
        "parts": [
            {
                "name": "modulator",
                "role": "",
                "label_ref": "EOM",
                "evidence_quote": "",
                "reason": "visible label",
                "confidence": 0.5,
            }
        ],
        "connections": [],
        "evidence_quote": "",
        "reason": "identified from the image",
        "confidence": 0.5,
    }
    agent = ApparatusSemanticsAgent()

    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(document_id="doc_1", figures=[figure])

    warnings = [
        issue
        for issue in result.validation_issues
        if issue.rule_id == "inner_labels_not_covered"
    ]
    assert len(warnings) == 1
    assert warnings[0].severity == "warning"
    assert "PBS" in warnings[0].message


def test_vision_response_schema_declares_label_ref():
    properties = APPARATUS_RESPONSE_SCHEMA["properties"]["parts"]["items"]["properties"]
    assert properties["label_ref"] == {"type": ["string", "null"]}
