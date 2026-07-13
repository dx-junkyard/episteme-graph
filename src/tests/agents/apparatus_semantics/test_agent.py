"""Integration tests for ApparatusSemanticsAgent with a mocked LLM client."""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.apparatus_semantics.agent import ApparatusSemanticsAgent
from episteme_graph.agents.apparatus_semantics.schema import (
    ApparatusSemanticsResult,
    FigureImageInput,
    LibraryCandidate,
)

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


def _figure(
    figure_id: str = "fig_1",
    caption: str = "Schematic of the drift chamber assembly.",
    nearby=None,
    image_bytes=_PNG_BYTES,
) -> FigureImageInput:
    return FigureImageInput(
        figure_id=figure_id,
        figure_key=figure_id,
        figure_label="Figure 1",
        caption_text=caption,
        image_bytes=image_bytes,
        nearby_text=nearby if nearby is not None else [
            "The ion source injects particles into the drift tube.",
        ],
    )


def _candidate(entry_id="lib_1", version_no=2) -> LibraryCandidate:
    return LibraryCandidate(
        entry_id=entry_id,
        version_no=version_no,
        name="Drift chamber",
        aliases=["TOF chamber"],
        summary="A vacuum chamber used to measure particle arrival time.",
        body={
            "typical_parts": [{"name": "ion source", "role": "injects particles"}],
            "visual_cues": ["cylindrical chamber with radial flanges"],
        },
    )


def _raw_response(**overrides) -> dict:
    base = {
        "apparatus_name_candidate": "Drift chamber",
        "matched_library_entry_id": None,
        "match_status": "novel",
        "parts": [
            {
                "name": "ion source",
                "role": "injects particles",
                "evidence_quote": "ion source injects particles into the drift tube",
                "reason": "named in nearby text",
                "confidence": 0.8,
            },
        ],
        "connections": [],
        "evidence_quote": "drift chamber assembly",
        "reason": "identified from image and caption",
        "confidence": 0.75,
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------
# Normal identification
# ------------------------------------------------------------------

def test_matched_status_with_library_candidate():
    agent = ApparatusSemanticsAgent()
    figure = _figure()
    candidate = _candidate(entry_id="lib_1", version_no=2)
    response = _raw_response(match_status="matched", matched_library_entry_id="lib_1")

    with patch.object(agent._llm_client, "generate", return_value=response) as mock_llm:
        result = agent.run(
            document_id="doc_test",
            figures=[figure],
            library_candidates={"fig_1": [candidate]},
        )

    assert isinstance(result, ApparatusSemanticsResult)
    assert mock_llm.call_count == 1
    # images must be passed through to the (mocked) vision call
    _, kwargs = mock_llm.call_args
    assert kwargs.get("images") and kwargs["images"][0]["data_base64"]

    record = result.apparatus_records[0]
    assert record.match_status == "matched"
    assert record.matched_library_entry_id == "lib_1"
    # version_no is trusted from the candidate list, not the (absent) LLM field
    assert record.matched_library_version_no == 2
    assert record.source_backing_status == "partially_source_backed"
    assert record.review_status == "review_required"
    assert record.repair_failed is False


def test_novel_status_without_library_candidates_degrades_gracefully():
    """library_candidates absent/empty is a fully supported input (design principle #5)."""
    agent = ApparatusSemanticsAgent()
    figure = _figure()
    response = _raw_response(match_status="novel", matched_library_entry_id=None)

    with patch.object(agent._llm_client, "generate", return_value=response) as mock_llm:
        result = agent.run(document_id="doc_test", figures=[figure])  # no library_candidates kwarg

    assert mock_llm.call_count == 1
    record = result.apparatus_records[0]
    assert record.match_status == "novel"
    assert record.matched_library_entry_id is None


def test_unknown_status_from_llm_is_preserved():
    agent = ApparatusSemanticsAgent()
    figure = _figure(caption="An unlabeled diagram.", nearby=[])
    response = _raw_response(
        apparatus_name_candidate="",
        match_status="unknown",
        evidence_quote="",
        parts=[],
        confidence=0.1,
    )
    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})

    record = result.apparatus_records[0]
    assert record.match_status == "unknown"
    assert record.repair_failed is False


# ------------------------------------------------------------------
# Validation → repair loop
# ------------------------------------------------------------------

def test_validation_failure_then_repair_success():
    agent = ApparatusSemanticsAgent()
    figure = _figure()
    bad = _raw_response(match_status="not_a_real_status")
    fixed = _raw_response(match_status="novel", matched_library_entry_id=None)

    with patch.object(agent._llm_client, "generate", side_effect=[bad, fixed]) as mock_llm:
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})

    assert mock_llm.call_count == 2
    record = result.apparatus_records[0]
    assert record.match_status == "novel"
    assert record.repair_failed is False


def test_repair_exhausted_falls_back_to_unknown():
    agent = ApparatusSemanticsAgent()
    figure = _figure()
    always_bad = _raw_response(match_status="still_not_valid")

    with patch.object(agent._llm_client, "generate", return_value=always_bad) as mock_llm:
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})

    # initial call + 2 repair attempts
    assert mock_llm.call_count == 3
    record = result.apparatus_records[0]
    assert record.match_status == "unknown"
    assert record.confidence == 0.0
    assert record.repair_failed is True
    assert record.review_status == "review_required"


def test_matched_entry_id_not_in_candidates_triggers_repair():
    agent = ApparatusSemanticsAgent()
    figure = _figure()
    candidate = _candidate(entry_id="lib_1", version_no=1)
    bad = _raw_response(match_status="matched", matched_library_entry_id="lib_nonexistent")
    fixed = _raw_response(match_status="matched", matched_library_entry_id="lib_1")

    with patch.object(agent._llm_client, "generate", side_effect=[bad, fixed]) as mock_llm:
        result = agent.run(
            document_id="doc_test",
            figures=[figure],
            library_candidates={"fig_1": [candidate]},
        )

    assert mock_llm.call_count == 2
    record = result.apparatus_records[0]
    assert record.matched_library_entry_id == "lib_1"


# ------------------------------------------------------------------
# image_bytes is None → never call the LLM
# ------------------------------------------------------------------

def test_image_bytes_none_skips_llm_call():
    agent = ApparatusSemanticsAgent()
    figure = _figure(image_bytes=None)

    with patch.object(agent._llm_client, "generate") as mock_llm:
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})

    mock_llm.assert_not_called()
    record = result.apparatus_records[0]
    assert record.match_status == "unknown"
    assert record.reason == "image_unavailable"
    assert record.confidence == 0.0
    assert record.source_backing_status != "source_backed"


# ------------------------------------------------------------------
# LLM call failure (not a validation issue — the call itself raises)
# ------------------------------------------------------------------

def test_llm_call_exception_produces_unknown_fallback_record():
    agent = ApparatusSemanticsAgent()
    figure = _figure()

    with patch.object(agent._llm_client, "generate", side_effect=RuntimeError("vision call failed")):
        result = agent.run(document_id="doc_test", figures=[figure], library_candidates={})

    record = result.apparatus_records[0]
    assert record.match_status == "unknown"
    assert record.confidence == 0.0
    assert "llm_call_failed" in record.reason
    assert record.repair_failed is False


# ------------------------------------------------------------------
# Guardrail: source_backed is never assigned, across every code path
# ------------------------------------------------------------------

def test_source_backed_never_assigned_across_all_paths():
    agent = ApparatusSemanticsAgent()

    figures = [
        _figure(figure_id="fig_matched"),
        _figure(figure_id="fig_novel"),
        _figure(figure_id="fig_no_image", image_bytes=None),
        _figure(figure_id="fig_call_fails"),
        _figure(figure_id="fig_repair_exhausted"),
    ]
    responses = {
        "fig_matched": _raw_response(match_status="matched", matched_library_entry_id="lib_1"),
        "fig_novel": _raw_response(match_status="novel"),
        "fig_repair_exhausted": _raw_response(match_status="bogus_status"),
    }

    def fake_generate(messages, response_schema=None, images=None):
        # crude figure_id sniffing from the rendered prompt content, good enough for this test
        content = "\n".join(str(m.get("content", "")) for m in messages)
        for figure_id, response in responses.items():
            if figure_id in content:
                return response
        if "fig_call_fails" in content:
            raise RuntimeError("boom")
        return _raw_response(match_status="unknown")

    with patch.object(agent._llm_client, "generate", side_effect=fake_generate):
        result = agent.run(
            document_id="doc_test",
            figures=figures,
            library_candidates={"fig_matched": [_candidate(entry_id="lib_1")]},
        )

    assert len(result.apparatus_records) == 5
    for record in result.apparatus_records:
        assert record.source_backing_status != "source_backed"
        assert record.review_status == "review_required"


# ------------------------------------------------------------------
# Cartridge handling — must not crash on an unknown cartridge_id
# ------------------------------------------------------------------

def test_unknown_cartridge_id_does_not_crash():
    agent = ApparatusSemanticsAgent()
    figure = _figure()
    response = _raw_response(match_status="novel")

    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(
            document_id="doc_test",
            figures=[figure],
            library_candidates={},
            cartridge_id="definitely_not_a_real_cartridge",
        )

    assert result.cartridge_id == "definitely_not_a_real_cartridge"
    assert result.apparatus_records[0].match_status == "novel"


def test_no_cartridge_id_works_standalone():
    agent = ApparatusSemanticsAgent()
    figure = _figure()
    response = _raw_response(match_status="novel")

    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(document_id="doc_test", figures=[figure])

    assert result.cartridge_id is None
    assert result.apparatus_records[0].match_status == "novel"


# ------------------------------------------------------------------
# Multiple figures in one run
# ------------------------------------------------------------------

def test_run_processes_multiple_figures_independently():
    agent = ApparatusSemanticsAgent()
    figures = [_figure(figure_id="fig_1"), _figure(figure_id="fig_2", image_bytes=None)]
    response = _raw_response(match_status="novel")

    with patch.object(agent._llm_client, "generate", return_value=response) as mock_llm:
        result = agent.run(document_id="doc_test", figures=figures, library_candidates={})

    assert mock_llm.call_count == 1  # only fig_1 has an image
    ids = {r.figure_id for r in result.apparatus_records}
    assert ids == {"fig_1", "fig_2"}
    by_id = {r.figure_id: r for r in result.apparatus_records}
    assert by_id["fig_1"].match_status == "novel"
    assert by_id["fig_2"].match_status == "unknown"
    assert by_id["fig_2"].reason == "image_unavailable"
