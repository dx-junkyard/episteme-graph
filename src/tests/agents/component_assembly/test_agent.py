"""Integration tests for ComponentAssemblyAgent with mocked LLM."""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.claim_qualification.schema import ClaimQualificationResult, QualifiedSpanRecord
from episteme_graph.agents.component_assembly.agent import ComponentAssemblyAgent
from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult
from episteme_graph.agents.dsl_linking.schema import DSLEdge, DSLLinkingResult, DSLNode, DSL_VERSION
from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    EquationConfidencePolicy,
    EquationRecord,
    EquationReconstruction,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
)
from episteme_graph.agents.thesis_reconstruction.schema import ThesisReconstructionResult, THESIS_VERSION


def _claim(span_id, block_id, text, claim_type):
    return QualifiedSpanRecord(
        span_id=span_id,
        block_id=block_id,
        section_id="sec_1",
        text=text,
        role_labels=[claim_type],
        qualification={
            "status": "accepted",
            "claim_tier": "paper_core",
            "claim_type_candidate": claim_type,
            "granularity": "good",
            "evidence_adequacy": "sufficient",
            "reviewability": "good",
        },
        edit_suggestions={},
        reason="mock",
        confidence=0.9,
    )


def _qualified():
    return ClaimQualificationResult(
        "doc_test",
        None,
        [
            _claim("s1", "b1", "Heavy quark limit is assumed.", "assumption"),
            _claim("s2", "b2", "The relation integrates to the total-rate sum rule.", "derivation_step"),
            _claim("s3", "b3", "Form factors dominate the uncertainty.", "uncertainty"),
            _claim("s4", "b4", "The sum rule checks experimental consistency.", "diagnostic_claim"),
        ],
        [],
        [],
        {"accepted": 4},
    )


def _make_equation_record(equation_id, block_id, label, text, equation_type, summary, defined_symbol, input_eq_ids):
    source_extraction = EquationSourceExtraction(
        raw_text=text,
        latex=None,
        plain_text=text,
        source_location={"page": 1, "section_id": "sec_1", "block_id": block_id, "bbox": None},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
    )
    reconstruction = EquationReconstruction.make_none()
    semantics = EquationSemantics(
        equation_type=equation_type,
        secondary_types=[],
        semantic_status="source_backed",
        confidence=0.9,
        reason=summary,
        defined_symbols=[DefinedSymbol(defined_symbol, "used", None)] if defined_symbol else [],
        used_symbols=[],
        assumptions=[],
        input_equation_ids=input_eq_ids,
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=[],
        linked_claim_ids=[],
        summary=summary,
        review_flags=[],
    )
    confidence_policy = EquationConfidencePolicy.derive(source_extraction, reconstruction, semantics)
    return EquationRecord(
        equation_id=equation_id,
        document_id="doc_test",
        label=label,
        candidate_trace_ids=[],
        source_extraction=source_extraction,
        reconstruction=reconstruction,
        semantics=semantics,
        confidence_policy=confidence_policy,
    )


def _equations():
    record = _make_equation_record(
        "eq_3_14", "e1", "3.14", "R Lambda c = RD + RD*",
        "relation", "Total-rate sum rule relation.",
        "R Lambda c", ["eq_1_2"],
    )
    return EquationSemanticsResult("doc_test", None, [], [record])


def _thesis():
    return ThesisReconstructionResult(
        "doc_test",
        THESIS_VERSION,
        None,
        {"text": "A sum rule relates R Lambda c, RD, and RD*.", "claim_ids": ["claim:b2:s2"], "equation_ids": ["eq_3_14"], "evidence_block_ids": ["b2"], "confidence": 0.9},
        [],
        {
            "direct_supports": [{"text": "Derivation support.", "claim_ids": ["claim:b2:s2"], "equation_ids": ["eq_3_14"], "support_type": "derivation_support", "confidence": 0.8}],
            "assumptions": [{"text": "Heavy quark limit.", "claim_ids": ["claim:b1:s1"], "equation_ids": [], "confidence": 0.8}],
            "uncertainty_sources": [{"text": "Form factors dominate uncertainty.", "claim_ids": ["claim:b3:s3"], "equation_ids": [], "confidence": 0.8}],
        },
        [],
        [],
        [],
        0.9,
    )


def _dsl():
    return DSLLinkingResult(
        "doc_test",
        DSL_VERSION,
        [
            DSLNode("n1", "Approximation", "heavy_quark_limit", "claim", {"claim_ids": ["claim:b1:s1"], "equation_ids": [], "thesis_refs": ["support:assumptions:0"]}, "assumption", 0.9),
            DSLNode("n2", "Relation", "total_rate_sum_rule", "mixed", {"claim_ids": ["claim:b2:s2"], "equation_ids": ["eq_3_14"], "thesis_refs": ["central_thesis"]}, "relation", 0.9),
            DSLNode("n3", "UncertaintySource", "form_factor_uncertainty", "claim", {"claim_ids": ["claim:b3:s3"], "equation_ids": [], "thesis_refs": ["support:uncertainty_sources:0"]}, "uncertainty", 0.8),
            DSLNode("n4", "Diagnostic", "experimental_consistency_check", "claim", {"claim_ids": ["claim:b4:s4"], "equation_ids": [], "thesis_refs": []}, "diagnostic", 0.8),
        ],
        [
            DSLEdge("e1", "n1", "n2", "REQUIRES", "assumes", "+", {"claim_ids": ["claim:b1:s1"], "equation_ids": ["eq_3_14"], "thesis_refs": ["support:assumptions:0"]}, "assumption required", 0.9),
            DSLEdge("e2", "n3", "n2", "CORRELATES", "contributes_to_uncertainty", "?", {"claim_ids": ["claim:b3:s3"], "equation_ids": [], "thesis_refs": ["support:uncertainty_sources:0"]}, "uncertainty qualifies", 0.8),
        ],
        [],
        [],
        0.86,
    )


def _evidence(claim_ids=None, equation_ids=None, thesis_refs=None, node_ids=None, edge_ids=None):
    return {
        "claim_ids": claim_ids or [],
        "equation_ids": equation_ids or [],
        "thesis_refs": thesis_refs or [],
        "dsl_refs": {"node_ids": node_ids or [], "edge_ids": edge_ids or []},
    }


def _valid_response():
    return {
        "document_id": "doc_test",
        "components_version": "v1",
        "components": [
            {
                "component_id": "comp_relation",
                "component_type": "RelationComponent",
                "label": "total-rate sum rule",
                "summary": "Reusable relation connecting R Lambda c to RD and RD*.",
                "inputs": [{"text": "heavy quark limit", "claim_ids": ["claim:b1:s1"]}],
                "outputs": [{"text": "R Lambda c relation", "equation_ids": ["eq_3_14"]}],
                "preconditions": [{"text": "heavy quark limit is assumed"}],
                "cautions": [{"text": "uncertainty must be propagated"}],
                "dependencies": [{"dependency_type": "requires", "component_refs": ["comp_assumption"], "reason": "requires the approximation"}],
                "evidence_refs": _evidence(["claim:b2:s2"], ["eq_3_14"], ["central_thesis"], ["n2"], ["e1"]),
                "linked_claim_ids": ["claim:b2:s2"],
                "linked_equation_ids": ["eq_3_14"],
                "linked_evidence_ids": ["ev_0001"],
                "linked_derivation_ids": ["derivation_eq_3_14"],
                "linked_dsl_node_ids": ["n2"],
                "linked_dsl_edge_ids": ["e1"],
                "review_status": "teacher_review_required",
                "teaching_takeaway": "Use the relation as a reusable derivation unit.",
                "source_scope": {"section_id": "sec_1"},
                "assumptions": ["heavy quark limit"],
                "approximations": [],
                "reason": "Relation and thesis support the reusable component.",
                "confidence": 0.9,
                "review_notes": [],
            },
            {
                "component_id": "comp_assumption",
                "component_type": "AssumptionComponent",
                "label": "heavy quark limit",
                "summary": "Assumption used by the derivation.",
                "inputs": [],
                "outputs": [{"text": "approximation context", "claim_ids": ["claim:b1:s1"]}],
                "preconditions": [],
                "cautions": [],
                "dependencies": [],
                "evidence_refs": _evidence(["claim:b1:s1"], [], ["support:assumptions:0"], ["n1"], []),
                "reason": "Assumption is explicit.",
                "confidence": 0.86,
                "review_notes": [],
            },
            {
                "component_id": "comp_uncertainty",
                "component_type": "UncertaintyComponent",
                "label": "form-factor uncertainty",
                "summary": "Uncertainty source that qualifies the relation.",
                "inputs": [{"text": "form-factor model", "claim_ids": ["claim:b3:s3"]}],
                "outputs": [{"text": "uncertainty on total-rate relation"}],
                "preconditions": [],
                "cautions": [{"text": "propagate to relation component"}],
                "dependencies": [{"dependency_type": "propagates_uncertainty_to", "component_refs": ["comp_relation"], "reason": "qualifies relation"}],
                "evidence_refs": _evidence(["claim:b3:s3"], [], ["support:uncertainty_sources:0"], ["n3"], ["e2"]),
                "reason": "Uncertainty node qualifies relation.",
                "confidence": 0.82,
                "review_notes": [],
            },
            {
                "component_id": "comp_diagnostic",
                "component_type": "DiagnosticComponent",
                "label": "experimental consistency check",
                "summary": "Diagnostic use of the relation.",
                "inputs": [{"text": "sum rule"}],
                "outputs": [{"text": "consistency check"}],
                "preconditions": [],
                "cautions": [],
                "dependencies": [{"dependency_type": "depends_on", "component_refs": ["comp_relation"], "reason": "diagnostic uses relation"}],
                "evidence_refs": _evidence(["claim:b4:s4"], [], [], ["n4"], []),
                "reason": "Claim describes a diagnostic role.",
                "confidence": 0.8,
                "review_notes": [],
            },
        ],
        "assembly_hints": [{"hint_type": "candidate_core_component", "component_ids": ["comp_relation"], "reason": "central relation"}],
        "review_notes": [],
        "confidence": 0.86,
    }


def test_run_returns_components():
    agent = ComponentAssemblyAgent()
    with patch.object(agent._llm_client, "generate", return_value=_valid_response()):
        result = agent.run(_qualified(), equations=_equations(), thesis=_thesis(), dsl=_dsl())
    assert isinstance(result, ComponentAssemblyResult)
    assert len(result.components) == 4
    assert any(c.component_type == "RelationComponent" for c in result.components)
    assert any(c.component_type == "UncertaintyComponent" for c in result.components)
    assert result.components[0].evidence_refs["dsl_refs"]["node_ids"] == ["n2"]
    assert result.components[0].linked_claim_ids == ["claim:b2:s2"]
    assert result.components[0].linked_equation_ids == ["eq_3_14"]
    assert result.components[0].linked_derivation_ids == ["derivation_eq_3_14"]
    assert result.components[0].review_status == "teacher_review_required"
    assert result.components[0].source_scope == {"section_id": "sec_1"}
    assert not [i for i in result.validation_issues if i.severity == "error"]


def test_repair_called_on_invalid_component_type():
    agent = ComponentAssemblyAgent()
    bad = _valid_response()
    bad["components"] = [{**bad["components"][0], "component_type": "BadComponent"}]
    with patch.object(agent._llm_client, "generate", side_effect=[bad, _valid_response()]) as mocked:
        result = agent.run(_qualified(), equations=_equations(), thesis=_thesis(), dsl=_dsl())
    assert mocked.call_count >= 2
    assert result.components[0].component_type == "RelationComponent"


def test_run_normalizes_qualified_dependency_without_repair():
    agent = ComponentAssemblyAgent()
    response = _valid_response()
    response["components"][2]["dependencies"] = [
        {"dependency_type": "qualified", "component_refs": ["comp_relation"], "reason": "qualifies relation"}
    ]

    with patch.object(agent._llm_client, "generate", return_value=response) as mocked:
        result = agent.run(_qualified(), equations=_equations(), thesis=_thesis(), dsl=_dsl())

    assert mocked.call_count == 1
    assert result.components[2].dependencies[0]["dependency_type"] == "qualifies"
    assert not [i for i in result.validation_issues if i.rule_id == "invalid_dependency_type"]


def test_run_enriches_component_equation_roles_and_internal_flow():
    agent = ComponentAssemblyAgent()
    response = _valid_response()
    response["components"][0]["input_equation_ids"] = []
    response["components"][0]["output_equation_ids"] = []
    response["components"][0]["internal_flow"] = []

    with patch.object(agent._llm_client, "generate", return_value=response):
        result = agent.run(_qualified(), equations=_equations(), thesis=_thesis(), dsl=_dsl())

    component = result.components[0]
    assert component.output_equation_ids == ["eq_3_14"]
    assert component.internal_flow


def test_llm_failure_returns_fallback():
    agent = ComponentAssemblyAgent()
    with patch.object(agent._llm_client, "generate", side_effect=RuntimeError("LLM error")):
        result = agent.run(_qualified(), equations=_equations(), thesis=_thesis(), dsl=_dsl())
    assert result.confidence == 0.0
    assert result.validation_issues[0].rule_id == "component_assembly_failed"
