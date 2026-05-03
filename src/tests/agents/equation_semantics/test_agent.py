"""Integration tests for EquationSemanticsAgent with mocked LLM."""
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


def _response(equation_id, block_id, label, text, primary, summary, symbols=None, assumptions=None, from_equations=None):
    return {
        "equation_id": equation_id,
        "block_id": block_id,
        "section_id": "sec_1",
        "section_title": "Formulation",
        "label": label,
        "text": text,
        "latex": None,
        "plain_text": text,
        "equation_role": {
            "primary": primary,
            "secondary": [],
            "confidence": 0.9,
            "reason": "mocked",
        },
        "defined_symbols": symbols or [],
        "local_assumptions": assumptions or [],
        "derivation_links": {
            "from_equations": from_equations or [],
            "to_equations": [],
            "linked_text_spans": [],
        },
        "summary": summary,
        "review_flags": [],
    }


def test_run_recovers_definition_transformation_and_approximation():
    agent = EquationSemanticsAgent()
    responses = [
        _response(
            "eq_1_1",
            "e1",
            "1.1",
            "N = a + b (1.1)",
            "equation_definition",
            "Prefactor definition for N.",
            symbols=[{"symbol": "N", "definition_status": "defined", "evidence_text": "where the prefactor is defined as"}],
        ),
        _response(
            "eq_1_2",
            "e2",
            "1.2",
            "∫ f dx = F (1.2)",
            "equation_transformation",
            "Integral transformation derived from eq. (1.1).",
            from_equations=["eq_1_1"],
        ),
        _response(
            "eq_1_3",
            "e3",
            "1.3",
            "Gamma_Lambdac = Gamma_D + Gamma_Dstar (1.3)",
            "equation_approximation",
            "Zero-recoil approximation for decay rates.",
            assumptions=[{"text": "In the zero-recoil limit", "source_block_ids": ["b4"]}],
        ),
    ]
    with patch.object(agent._llm_client, "generate", side_effect=responses):
        result = agent.run(_structure(), skeleton=_skeleton())

    assert isinstance(result, EquationSemanticsResult)
    assert [e.equation_role.primary for e in result.equations] == [
        "equation_definition",
        "equation_transformation",
        "equation_approximation",
    ]
    assert result.equations[0].defined_symbols[0].symbol == "N"
    assert result.equations[1].derivation_links.from_equations == ["eq_1_1"]
    assert "zero-recoil" in result.equations[2].local_assumptions[0].text


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
        "eq_3_14",
        "e1",
        "3.14",
        "RΛc / RΛcSM = 1/4 RD / RDSM + 3/4 RD* / RD*SM (3.14)",
        "equation_relation",
        "Sum rule relation between RΛc, RD, and RD*.",
        symbols=[
            {"symbol": "RΛc", "definition_status": "used", "evidence_text": None},
            {"symbol": "RD", "definition_status": "used", "evidence_text": None},
        ],
    )
    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(structure)

    assert result.equations[0].equation_role.primary == "equation_relation"
    assert "Sum rule" in result.equations[0].summary


def test_repair_called_on_invalid_role():
    agent = EquationSemanticsAgent()
    bad = _response("eq_1_1", "e1", "1.1", "N = a + b (1.1)", "bad", "Bad summary")
    fixed = _response(
        "eq_1_1",
        "e1",
        "1.1",
        "N = a + b (1.1)",
        "equation_definition",
        "Prefactor definition for N.",
        symbols=[{"symbol": "N", "definition_status": "defined", "evidence_text": "where the prefactor is defined as"}],
    )
    second = _response("eq_1_2", "e2", "1.2", "∫ f dx = F (1.2)", "equation_transformation", "Integral transformation.", from_equations=["eq_1_1"])
    third = _response("eq_1_3", "e3", "1.3", "Gamma_Lambdac = Gamma_D + Gamma_Dstar (1.3)", "equation_approximation", "Zero-recoil approximation.", assumptions=[{"text": "zero-recoil limit", "source_block_ids": ["b4"]}])
    with patch.object(agent._llm_client, "generate", side_effect=[bad, fixed, second, third]) as mocked:
        result = agent.run(_structure())

    assert mocked.call_count >= 4
    assert result.equations[0].equation_role.primary == "equation_definition"


def test_llm_failure_returns_unknown_fallback_record():
    agent = EquationSemanticsAgent()
    with patch.object(agent._llm_client, "generate", side_effect=RuntimeError("LLM error")):
        result = agent.run(_structure(), config={"max_equations": 1})

    assert result.equations[0].equation_role.primary == "unknown"
    assert result.equations[0].equation_role.confidence == 0.0
