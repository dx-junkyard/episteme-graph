"""Integration tests for EquationSemanticsAgent with mocked LLM (Issue #245 refactor)."""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.document_structure.schema import (
    DocumentMetadata,
    DocumentStructureResult,
    Section,
    TypedBlock,
)
from episteme_graph.agents.equation_semantics.agent import EquationSemanticsAgent
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.paper_skeleton.schema import LogicalBlock, PaperSkeletonResult, SKELETON_VERSION


def _typed(block_id, text, block_type, order):
    b = TypedBlock(block_id, 1, order, text, block_type)
    b.section_id = "sec_1"
    return b


def _structure():
    return DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Formulation", 1, 1, 1)],
        blocks=[
            _typed("b0", "where the prefactor is defined as", "body_paragraph", 0),
            _typed("e1", "N = a + b (1.1)", "equation_block", 1),
            _typed("b2", "By integrating both sides of eq. (1.1), the relation becomes", "body_paragraph", 2),
            _typed("e2", "∫ f dx = F (1.2)", "equation_block", 3),
            _typed("b4", "In the zero-recoil limit, eq. (1.2) is simplified as", "body_paragraph", 4),
            _typed("e3", "Gamma_Lambdac = Gamma_D + Gamma_Dstar (1.3)", "equation_block", 5),
        ],
    )


def _skeleton():
    return PaperSkeletonResult(
        document_id="doc_test",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "goal", "evidence_block_ids": ["e1"], "reason": "", "confidence": 0.8},
        central_question={"text": "question", "evidence_block_ids": ["e1"], "reason": "", "confidence": 0.8},
        headline_claim={"text": "headline", "evidence_block_ids": ["e1"], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[LogicalBlock("l1", "derivation", "Derivation", ["sec_1"], ["e1"], "summary", "reason", 0.8)],
        excluded_regions=[],
        review_notes=[],
        confidence=0.8,
    )


def _response(equation_id, block_id, label, text, eq_type, summary, symbols=None, assumptions=None, from_equations=None, sem_status="source_backed"):
    return {
        "equation_id": equation_id,
        "label": label,
        "equation_type": eq_type,
        "secondary_types": [],
        "semantic_status": sem_status,
        "confidence": 0.9,
        "reason": "mocked",
        "defined_symbols": symbols or [],
        "used_symbols": [],
        "assumptions": assumptions or [],
        "input_equation_ids": from_equations or [],
        "output_equation_ids": [],
        "linked_text_spans": [],
        "summary": summary,
        "review_flags": [],
    }


def test_run_recovers_definition_transformation_and_approximation():
    agent = EquationSemanticsAgent()
    responses = [
        _response(
            "eq_1_1", "e1", "1.1", "N = a + b (1.1)", "definition",
            "Prefactor definition for N.",
            symbols=[{"symbol": "N", "definition_status": "defined", "evidence_text": "where the prefactor is defined as"}],
        ),
        _response(
            "eq_1_2", "e2", "1.2", "∫ f dx = F (1.2)", "transformation",
            "Integral transformation derived from eq. (1.1).",
            from_equations=["eq_1_1"],
        ),
        _response(
            "eq_1_3", "e3", "1.3", "Gamma_Lambdac = Gamma_D + Gamma_Dstar (1.3)", "approximation",
            "Zero-recoil approximation for decay rates.",
            assumptions=["In the zero-recoil limit"],
        ),
    ]
    with patch.object(agent._llm_client, "generate", side_effect=responses):
        result = agent.run(_structure(), skeleton=_skeleton())

    assert isinstance(result, EquationSemanticsResult)
    assert [e.semantics.equation_type for e in result.equations] == [
        "definition", "transformation", "approximation",
    ]
    assert result.equations[0].semantics.defined_symbols[0].symbol == "N"
    assert result.equations[1].semantics.input_equation_ids == ["eq_1_1"]
    assert "zero-recoil" in result.equations[2].semantics.assumptions[0].lower()


def test_result_includes_equation_candidates():
    agent = EquationSemanticsAgent()
    response = _response("eq_1_1", "e1", "1.1", "N = a + b (1.1)", "definition", "Prefactor definition for N.")
    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(_structure(), config={"max_equations": 1})

    # candidates should include all blocks that were processed by the gate
    assert len(result.equation_candidates) >= 1
    accepted = [c for c in result.equation_candidates if c.acceptance_status == "accepted"]
    assert len(accepted) >= 1


def test_candidate_trace_ids_in_record():
    agent = EquationSemanticsAgent()
    response = _response("eq_1_1", "e1", "1.1", "N = a + b (1.1)", "definition", "Prefactor definition.")
    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(_structure(), config={"max_equations": 1})

    assert result.equations
    assert result.equations[0].candidate_trace_ids  # must not be empty


def test_fragment_only_blocks_are_not_processed_by_llm():
    """Fragment-only equation blocks should be gated out before LLM call."""
    structure = DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Results", 1, 1, 1)],
        blocks=[
            _typed("b0", "the term is", "body_paragraph", 0),
            _typed("e1", "+", "equation_block", 1),  # fragment-only
            _typed("e2", "N = a + b (1.1)", "equation_block", 2),  # valid
        ],
    )
    response = _response("eq_1_1", "e2", "1.1", "N = a + b (1.1)", "definition", "Prefactor definition.")
    with patch.object(agent := EquationSemanticsAgent(), "_llm_client") as mock_llm:
        mock_llm.generate.return_value = response
        result = agent.run(structure)

    # fragment should be in candidates but rejected
    fragment_candidates = [c for c in result.equation_candidates if c.source_location["block_id"] == "e1"]
    assert fragment_candidates
    assert fragment_candidates[0].acceptance_status == "needs_merge"

    # LLM should only be called for the valid equation
    assert mock_llm.generate.call_count == 1


def test_label_only_blocks_with_matched_label_become_provisional():
    """Label-only blocks with a matched label → provisional (issue #259).

    The input_builder extracts the label "3.14" from "(3.14)" and sets
    matched_label, making this a high-signal provisional candidate instead
    of a rejected one.
    """
    structure = DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Results", 1, 1, 1)],
        blocks=[
            _typed("e_label", "(3.14)", "equation_block", 0),
            _typed("e_valid", "N = a + b (1.1)", "equation_block", 1),
        ],
    )
    response = _response("eq_1_1", "e_valid", "1.1", "N = a + b", "definition", "Definition.")
    with patch.object(agent := EquationSemanticsAgent(), "_llm_client") as mock_llm:
        mock_llm.generate.return_value = response
        result = agent.run(structure)

    label_candidates = [c for c in result.equation_candidates if c.source_location["block_id"] == "e_label"]
    assert label_candidates[0].extraction_status == "label_only"
    # matched_label present → provisional (high-signal, not outright rejected)
    assert label_candidates[0].acceptance_status == "provisional"
    # LLM is called only for accepted block, not for provisional
    assert mock_llm.generate.call_count == 1
    # Provisional EquationRecord is generated with can_support_claim=False
    provisional_records = [r for r in result.equations if r.confidence_policy.can_support_claim is False
                           and r.equation_id.startswith("eq_provisional")]
    assert len(provisional_records) == 1
    assert provisional_records[0].label == "3.14"


def test_relation_or_result_summary_is_preserved():
    agent = EquationSemanticsAgent()
    structure = DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Results", 1, 1, 1)],
        blocks=[
            _typed("b0", "the sum rule for the total decay rates is derived as", "body_paragraph", 0),
            _typed("e1", "RΛc / RΛcSM = 1/4 RD / RDSM + 3/4 RD* / RD*SM (3.14)", "equation_block", 1),
        ],
    )
    response = _response(
        "eq_3_14", "e1", "3.14", "RΛc / RΛcSM = 1/4 RD / RDSM + 3/4 RD* / RD*SM (3.14)",
        "relation", "Sum rule relation between RΛc, RD, and RD*.",
        symbols=[
            {"symbol": "RΛc", "definition_status": "used", "evidence_text": None},
            {"symbol": "RD", "definition_status": "used", "evidence_text": None},
        ],
    )
    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(structure)

    assert result.equations[0].semantics.equation_type == "relation"
    assert "Sum rule" in result.equations[0].semantics.summary


def test_repair_called_on_invalid_type():
    agent = EquationSemanticsAgent()
    bad = _response("eq_1_1", "e1", "1.1", "N = a + b (1.1)", "bad_type", "Bad summary")
    fixed = _response(
        "eq_1_1", "e1", "1.1", "N = a + b (1.1)", "definition",
        "Prefactor definition for N.",
        symbols=[{"symbol": "N", "definition_status": "defined", "evidence_text": "where the prefactor is defined as"}],
    )
    second = _response("eq_1_2", "e2", "1.2", "∫ f dx = F (1.2)", "transformation", "Integral.", from_equations=["eq_1_1"])
    third = _response("eq_1_3", "e3", "1.3", "Gamma_Lambdac = Gamma_D + Gamma_Dstar (1.3)", "approximation", "Zero-recoil.", assumptions=["zero-recoil limit"])
    with patch.object(agent._llm_client, "generate", side_effect=[bad, fixed, second, third]) as mocked:
        result = agent.run(_structure())

    assert mocked.call_count >= 4
    assert result.equations[0].semantics.equation_type == "definition"


def test_llm_failure_returns_unknown_fallback_record():
    agent = EquationSemanticsAgent()
    with patch.object(agent._llm_client, "generate", side_effect=RuntimeError("LLM error")):
        result = agent.run(_structure(), config={"max_equations": 1})

    assert result.equations[0].semantics.equation_type == "unknown"
    assert result.equations[0].semantics.confidence == 0.0
    assert result.equations[0].confidence_policy.can_support_claim is False


def test_no_accepted_returns_provisional_only_no_llm():
    """全候補が accepted でない場合、LLM は呼ばれず provisional のみが返る (issue #259).

    "(3.14)" → matched_label あり → provisional EquationRecord が生成される
    "+"     → fragment, no matched_label → needs_merge (no EquationRecord)
    """
    structure = DocumentStructureResult(
        document_id="doc_test",
        source_file="/tmp/test.pdf",
        cartridge_id=None,
        metadata=DocumentMetadata(title="Test", pages=1),
        sections=[Section("sec_1", "Results", 1, 1, 1)],
        blocks=[
            _typed("e1", "(3.14)", "equation_block", 0),   # label_only + matched_label → provisional
            _typed("e2", "+", "equation_block", 1),         # fragment → needs_merge (no record)
        ],
    )
    agent = EquationSemanticsAgent()
    with patch.object(agent._llm_client, "generate") as mock_llm:
        result = agent.run(structure)

    mock_llm.assert_not_called()
    assert len(result.equation_candidates) == 2
    # "(3.14)" gets a provisional EquationRecord; "+" does not
    provisional_eq = [e for e in result.equations if e.confidence_policy.can_support_claim is False]
    assert len(provisional_eq) == 1
    assert provisional_eq[0].confidence_policy.must_not_treat_as_source_extracted is True
