"""Tests for thesis reconstruction schema helpers."""
import json

from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult, THESIS_VERSION


def test_to_json_round_trip():
    result = ThesisReconstructionResult(
        document_id="doc",
        thesis_version=THESIS_VERSION,
        cartridge_id=None,
        central_thesis={"text": "Thesis", "claim_ids": [], "equation_ids": [], "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        alternative_theses=[],
        support_structure={"direct_supports": []},
        excluded_from_core=[],
        thesis_graph_hints=[],
        review_notes=[],
        confidence=0.8,
    )
    restored = ThesisReconstructionResult.from_dict(json.loads(result.to_json()))
    assert restored.central_thesis["text"] == "Thesis"
