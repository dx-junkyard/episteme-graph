"""Tests for apparatus_components.py and ComponentAssemblyAgent's optional
``apparatus_semantics`` kwarg (image pipeline, design doc §5-5).
"""
from __future__ import annotations

from unittest.mock import patch

from episteme_graph.agents.apparatus_semantics.schema import (
    ApparatusConnection,
    ApparatusPart,
    ApparatusRecord,
    ApparatusSemanticsResult,
)
from episteme_graph.agents.claim_qualification.schema import (
    ClaimQualificationResult,
    QualifiedSpanRecord,
)
from episteme_graph.agents.component_assembly.agent import ComponentAssemblyAgent
from episteme_graph.agents.component_assembly.apparatus_components import (
    APPARATUS_COMPONENT_TYPE,
    build_apparatus_components,
)
from episteme_graph.agents.component_assembly.schema import CartridgeContext, ComponentRecord


# ---------------------------------------------------------------------------
# Fixtures shared by the unit tests (build_apparatus_components) and the
# agent-integration tests (ComponentAssemblyAgent.run).
# ---------------------------------------------------------------------------

def _matched_record(figure_id: str = "fig_1") -> ApparatusRecord:
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
                role="injects charged particles into the drift tube",
                evidence_quote="an ion source that injects charged particles",
                reason="named in nearby text",
                confidence=0.88,
            ),
            ApparatusPart(
                name="drift tube",
                role="field-free flight path",
                evidence_quote="drift tube",
                reason="named in caption",
                confidence=0.9,
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
        reason="Image and caption match the retrieved library candidate closely.",
        confidence=0.86,
        source_backing_status="partially_source_backed",
        review_status="review_required",
        repair_failed=False,
    )


def _unknown_record(figure_id: str = "fig_2") -> ApparatusRecord:
    return ApparatusRecord(
        figure_id=figure_id,
        figure_key=figure_id,
        apparatus_name_candidate="",
        matched_library_entry_id=None,
        matched_library_version_no=None,
        match_status="unknown",
        parts=[],
        connections=[],
        evidence_quote="",
        reason="",
        confidence=0.0,
        source_backing_status="inferred",
        review_status="review_required",
        repair_failed=True,
    )


def _apparatus_result(*records: ApparatusRecord) -> ApparatusSemanticsResult:
    return ApparatusSemanticsResult(
        document_id="doc_test",
        cartridge_id="particle_physics",
        apparatus_records=list(records),
    )


# ---------------------------------------------------------------------------
# build_apparatus_components: unit tests
# ---------------------------------------------------------------------------

def test_build_apparatus_components_returns_empty_for_none():
    assert build_apparatus_components(None, "doc_test") == []


def test_build_apparatus_components_returns_empty_for_no_records():
    result = ApparatusSemanticsResult(document_id="doc_test", cartridge_id=None)
    assert build_apparatus_components(result, "doc_test") == []


def test_build_apparatus_components_maps_matched_record():
    result = _apparatus_result(_matched_record())
    components = build_apparatus_components(result, "doc_test")

    assert len(components) == 1
    comp = components[0]
    assert isinstance(comp, ComponentRecord)
    assert comp.component_type == APPARATUS_COMPONENT_TYPE
    assert comp.label == "Time-of-flight drift chamber"
    assert comp.component_id.startswith("apparatus_")
    # parts -> inputs (design doc §5-5: no per-part child component in v1)
    assert len(comp.inputs) == 2
    assert comp.inputs[0]["name"] == "ion source"
    assert comp.inputs[0]["role"] == "injects charged particles into the drift tube"
    assert comp.inputs[0]["confidence"] == 0.88
    # connections -> internal_flow
    assert comp.internal_flow == [{
        "from": "ion source",
        "relation": "injects particles into",
        "to": "drift tube",
        "reason": "described in caption",
        "confidence": 0.8,
    }]
    # provenance is not dropped (P4): figure/library/evidence fields land in source_scope.
    assert comp.source_scope["figure_id"] == "fig_1"
    assert comp.source_scope["figure_key"] == "fig_1"
    assert comp.source_scope["matched_library_entry_id"] == "lib_entry_0001"
    assert comp.source_scope["matched_library_version_no"] == 3
    assert comp.source_scope["match_status"] == "matched"
    assert comp.source_scope["evidence_quote"] == "Schematic of the vacuum chamber assembly"
    assert comp.source_scope["document_id"] == "doc_test"
    assert comp.reason == "Image and caption match the retrieved library candidate closely."
    assert comp.confidence == 0.86


def test_build_apparatus_components_keeps_unknown_record_with_neutral_label():
    """match_status='unknown' must never be dropped (P4)."""
    result = _apparatus_result(_unknown_record())
    components = build_apparatus_components(result, "doc_test")

    assert len(components) == 1
    comp = components[0]
    assert comp.component_type == APPARATUS_COMPONENT_TYPE
    assert comp.label == "Unidentified apparatus (fig_2)"
    assert comp.source_scope["match_status"] == "unknown"
    assert comp.review_notes  # repair_failed note kept, not silently discarded


def test_build_apparatus_components_never_assigns_confirmed_status():
    """Design principle #2: vision-derived identification is candidate-only."""
    result = _apparatus_result(_matched_record(), _unknown_record())
    components = build_apparatus_components(result, "doc_test")

    for comp in components:
        assert comp.review_status == "teacher_review_required"
        assert comp.publish_ready is False
        assert comp.maturity_source in ("llm_proposed", "unknown")
        assert comp.maturity_source != "teacher_reviewed"


def test_build_apparatus_components_accepts_dict_input():
    """ApparatusSemanticsResult may arrive as a plain dict (e.g. reloaded from
    a stage artifact) — round-trips the same way as ApparatusSemanticsResult.from_dict."""
    result = _apparatus_result(_matched_record())
    components = build_apparatus_components(result.to_dict(), "doc_test")
    assert len(components) == 1
    assert components[0].component_type == APPARATUS_COMPONENT_TYPE


def test_build_apparatus_components_avoids_id_collision_with_existing_components():
    result = _apparatus_result(_matched_record(figure_id="fig_1"))
    components = build_apparatus_components(
        result, "doc_test", existing_component_ids={"apparatus_fig_1"},
    )
    assert len(components) == 1
    assert components[0].component_id != "apparatus_fig_1"
    assert components[0].component_id.startswith("apparatus_fig_1")


def test_build_apparatus_components_multiple_records_get_distinct_ids():
    result = _apparatus_result(_matched_record("fig_1"), _unknown_record("fig_2"))
    components = build_apparatus_components(result, "doc_test")
    ids = [c.component_id for c in components]
    assert len(ids) == len(set(ids)) == 2


def test_build_apparatus_components_records_cartridge_id_when_present():
    cartridge = CartridgeContext(
        cartridge_id="particle_physics",
        ontology={},
        component_types={},
        relation_types={},
        validation_rules={},
    )
    result = _apparatus_result(_matched_record())
    components = build_apparatus_components(result, "doc_test", cartridge=cartridge)
    assert components[0].source_scope["cartridge_id"] == "particle_physics"


def test_build_apparatus_components_works_without_cartridge():
    """Design principle #5: the pipeline must work standalone with no cartridge."""
    result = _apparatus_result(_matched_record())
    components = build_apparatus_components(result, "doc_test", cartridge=None)
    assert len(components) == 1
    assert components[0].source_scope["cartridge_id"] is None


# ---------------------------------------------------------------------------
# ComponentAssemblyAgent.run: apparatus_semantics kwarg integration
# ---------------------------------------------------------------------------

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
        [_claim("s1", "b1", "Heavy quark limit is assumed.", "assumption")],
        [],
        [],
        {"accepted": 1},
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
                "component_id": "comp_assumption",
                "component_type": "AssumptionComponent",
                "label": "heavy quark limit",
                "summary": "Assumption used by the derivation.",
                "inputs": [],
                "outputs": [{"text": "approximation context", "claim_ids": ["claim:b1:s1"]}],
                "preconditions": [],
                "cautions": [],
                "dependencies": [],
                "evidence_refs": _evidence(["claim:b1:s1"]),
                "reason": "Assumption is explicit.",
                "confidence": 0.86,
                "review_notes": [],
            },
        ],
        "assembly_hints": [],
        "review_notes": [],
        "confidence": 0.86,
    }


def test_run_without_apparatus_semantics_is_unaffected():
    """apparatus_semantics=None (the default) must leave existing behaviour untouched."""
    agent = ComponentAssemblyAgent()
    with patch.object(agent._llm_client, "generate", return_value=_valid_response()):
        result = agent.run(_qualified())
    assert len(result.components) == 1
    assert all(c.component_type != APPARATUS_COMPONENT_TYPE for c in result.components)
    assert "apparatus_component_count" not in result.diagnostics


def test_run_with_apparatus_semantics_appends_apparatus_components():
    agent = ComponentAssemblyAgent()
    apparatus = _apparatus_result(_matched_record())
    with patch.object(agent._llm_client, "generate", return_value=_valid_response()):
        result = agent.run(_qualified(), apparatus_semantics=apparatus)

    assert len(result.components) == 2
    apparatus_components = [c for c in result.components if c.component_type == APPARATUS_COMPONENT_TYPE]
    assert len(apparatus_components) == 1
    assert apparatus_components[0].label == "Time-of-flight drift chamber"
    assert result.diagnostics["apparatus_component_count"] == 1
    # No invalid_component_type error: 'apparatus' is a valid component_type
    # even without a cartridge loaded (agent-level default vocabulary).
    assert not [
        i for i in result.validation_issues
        if i.rule_id == "invalid_component_type"
    ]
    assert not [i for i in result.validation_issues if i.severity == "error"]


def test_run_with_apparatus_semantics_keeps_unknown_records():
    agent = ComponentAssemblyAgent()
    apparatus = _apparatus_result(_unknown_record())
    with patch.object(agent._llm_client, "generate", return_value=_valid_response()):
        result = agent.run(_qualified(), apparatus_semantics=apparatus)

    apparatus_components = [c for c in result.components if c.component_type == APPARATUS_COMPONENT_TYPE]
    assert len(apparatus_components) == 1
    assert apparatus_components[0].label == "Unidentified apparatus (fig_2)"


def test_run_with_empty_apparatus_semantics_is_a_no_op():
    agent = ComponentAssemblyAgent()
    empty_apparatus = ApparatusSemanticsResult(document_id="doc_test", cartridge_id=None)
    with patch.object(agent._llm_client, "generate", return_value=_valid_response()):
        result = agent.run(_qualified(), apparatus_semantics=empty_apparatus)

    assert len(result.components) == 1
    assert "apparatus_component_count" not in result.diagnostics
