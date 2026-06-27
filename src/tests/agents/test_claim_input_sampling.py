"""Cross-stage tests for the unified claim input sampling policy (issue #356).

thesis_reconstruction / dsl_linking / component_assembly must share the same
importance-based selection and record every dropped claim explicitly.
"""
from episteme_graph.agents.claim_qualification.schema import (
    ClaimQualificationResult,
    QualifiedSpanRecord,
)
from episteme_graph.agents.claim_selection import (
    RULE_CLAIM_INPUT_LIMIT_EXCEEDED,
    RULE_THESIS_REFERENCED_CLAIM_EXCLUDED,
)
from episteme_graph.agents.component_assembly.input_builder import (
    ComponentAssemblyInputBuilder,
)
from episteme_graph.agents.dsl_linking.agent import DSLLinkingAgent
from episteme_graph.agents.dsl_linking.input_builder import DSLLinkingInputBuilder
from episteme_graph.agents.dsl_linking.schema import DSLLinkingResult
from episteme_graph.agents.paper_skeleton.schema import (
    LogicalBlock,
    PaperSkeletonResult,
    SKELETON_VERSION,
)
from episteme_graph.agents.thesis_reconstruction.agent import (
    ThesisReconstructionAgent,
)
from episteme_graph.agents.thesis_reconstruction.input_builder import (
    ThesisReconstructionInputBuilder,
)
from episteme_graph.agents.thesis_reconstruction.schema import (
    SUPPORT_SECTIONS,
    ThesisReconstructionResult,
    THESIS_VERSION,
)


def _span(span_id, *, tier="background", confidence=0.5):
    return QualifiedSpanRecord(
        span_id=span_id,
        block_id=f"b_{span_id}",
        section_id="sec_1",
        text=f"claim text {span_id}",
        role_labels=["relation"],
        qualification={
            "status": "accepted",
            "claim_tier": tier,
            "claim_type_candidate": "relation",
            "granularity": "good",
            "evidence_adequacy": "sufficient",
            "reviewability": "good",
        },
        edit_suggestions={},
        reason="r",
        confidence=confidence,
    )


def _qualified(spans):
    return ClaimQualificationResult(
        "doc", None, list(spans), [], [], {"accepted": len(spans)}
    )


def _skeleton():
    return PaperSkeletonResult(
        document_id="doc",
        skeleton_version=SKELETON_VERSION,
        cartridge_id=None,
        paper_goal={"text": "goal", "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        central_question={"text": "q", "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        headline_claim={"text": "h", "evidence_block_ids": [], "reason": "", "confidence": 0.8},
        supporting_subclaims=[],
        logical_blocks=[
            LogicalBlock("l1", "derivation", "Derivation", ["sec_1"], [], "s", "r", 0.9)
        ],
        excluded_regions=[],
        review_notes=[],
        confidence=0.8,
    )


def _thesis(claim_ids):
    return ThesisReconstructionResult(
        document_id="doc",
        thesis_version=THESIS_VERSION,
        cartridge_id=None,
        central_thesis={
            "text": "central",
            "claim_ids": list(claim_ids),
            "equation_ids": [],
            "evidence_block_ids": [],
            "reason": "r",
            "confidence": 0.9,
        },
        alternative_theses=[],
        support_structure={section: [] for section in SUPPORT_SECTIONS},
        excluded_from_core=[],
        thesis_graph_hints=[],
        review_notes=[],
        confidence=0.9,
    )


_SPANS = [
    _span("s1", tier="background", confidence=0.9),
    _span("s2", tier="background", confidence=0.8),
    _span("s3", tier="paper_core", confidence=0.4),
    _span("s4", tier="meta", confidence=0.9),
]


def test_thesis_builder_keeps_paper_core_over_limit():
    llm_input = ThesisReconstructionInputBuilder().build(
        _skeleton(), _qualified(_SPANS), config={"max_claims": 2},
    )
    selected_spans = [c["span_id"] for c in llm_input.accepted_claims]
    assert "s3" in selected_spans  # paper_core survives
    assert len(selected_spans) == 2
    excluded_spans = {e["span_id"] for e in llm_input.excluded_from_pipeline_input}
    assert len(excluded_spans) == 2
    assert all(
        e["excluded_at_stage"] == "thesis_reconstruction"
        for e in llm_input.excluded_from_pipeline_input
    )


def test_dsl_builder_keeps_paper_core_over_limit():
    llm_input = DSLLinkingInputBuilder().build(
        _qualified(_SPANS), config={"max_claims": 2},
    )
    selected_spans = [c["span_id"] for c in llm_input.accepted_claims]
    assert "s3" in selected_spans
    assert len(llm_input.excluded_from_pipeline_input) == 2


def test_component_builder_keeps_paper_core_over_limit():
    llm_input = ComponentAssemblyInputBuilder().build(
        _qualified(_SPANS), config={"max_claims": 2},
    )
    selected_spans = [c["span_id"] for c in llm_input.accepted_claims]
    assert "s3" in selected_spans
    assert len(llm_input.excluded_from_pipeline_input) == 2
    assert all(
        e["excluded_at_stage"] == "component_assembly"
        for e in llm_input.excluded_from_pipeline_input
    )


def test_stages_share_the_same_selection_membership():
    """同じ入力・同じ上限なら 3 stage で同じ claim 集合が選ばれる."""
    thesis_in = ThesisReconstructionInputBuilder().build(
        _skeleton(), _qualified(_SPANS), config={"max_claims": 2},
    )
    dsl_in = DSLLinkingInputBuilder().build(
        _qualified(_SPANS), config={"max_claims": 2},
    )
    comp_in = ComponentAssemblyInputBuilder().build(
        _qualified(_SPANS), config={"max_claims": 2},
    )
    thesis_ids = {c["claim_id"] for c in thesis_in.accepted_claims}
    dsl_ids = {c["claim_id"] for c in dsl_in.accepted_claims}
    comp_ids = {c["claim_id"] for c in comp_in.accepted_claims}
    assert thesis_ids == dsl_ids == comp_ids


def test_thesis_referenced_exclusion_is_flagged_in_dsl_stage():
    # claim_id for span s4 (meta, will be dropped) referenced by thesis.
    dropped_claim_id = "claim:b_s4:s4"
    llm_input = DSLLinkingInputBuilder().build(
        _qualified(_SPANS),
        thesis=_thesis([dropped_claim_id]),
        config={"max_claims": 2},
    )
    flagged = [
        e for e in llm_input.excluded_from_pipeline_input
        if e["claim_id"] == dropped_claim_id
    ]
    assert flagged and flagged[0]["referenced_by_thesis"] is True


def test_no_exclusions_under_limit():
    llm_input = DSLLinkingInputBuilder().build(
        _qualified(_SPANS), config={"max_claims": 10},
    )
    assert llm_input.excluded_from_pipeline_input == []


def test_agent_records_exclusions_as_warnings():
    """除外があると result に excluded_from_pipeline_input + warning が付く."""
    llm_input = DSLLinkingInputBuilder().build(
        _qualified(_SPANS),
        thesis=_thesis(["claim:b_s4:s4"]),
        config={"max_claims": 2},
    )
    result = DSLLinkingResult(
        document_id="doc", dsl_version="v1", nodes=[], edges=[],
        graph_hints=[], review_notes=[], confidence=0.8,
    )
    DSLLinkingAgent._record_claim_exclusions(result, llm_input)
    assert len(result.excluded_from_pipeline_input) == 2
    rule_ids = [i.rule_id for i in result.validation_issues]
    assert RULE_CLAIM_INPUT_LIMIT_EXCEEDED in rule_ids
    assert RULE_THESIS_REFERENCED_CLAIM_EXCLUDED in rule_ids
    assert all(i.severity == "warning" for i in result.validation_issues)


def test_thesis_agent_records_exclusions_as_warnings():
    llm_input = ThesisReconstructionInputBuilder().build(
        _skeleton(), _qualified(_SPANS), config={"max_claims": 2},
    )
    result = _thesis([])
    ThesisReconstructionAgent._record_claim_exclusions(result, llm_input)
    assert len(result.excluded_from_pipeline_input) == 2
    assert RULE_CLAIM_INPUT_LIMIT_EXCEEDED in [
        i.rule_id for i in result.validation_issues
    ]


def test_exclusions_round_trip_via_dict():
    llm_input = DSLLinkingInputBuilder().build(
        _qualified(_SPANS), config={"max_claims": 2},
    )
    result = DSLLinkingResult(
        document_id="doc", dsl_version="v1", nodes=[], edges=[],
        graph_hints=[], review_notes=[], confidence=0.8,
        excluded_from_pipeline_input=llm_input.excluded_from_pipeline_input,
    )
    reloaded = DSLLinkingResult.from_dict(result.to_dict())
    assert reloaded.excluded_from_pipeline_input == llm_input.excluded_from_pipeline_input
