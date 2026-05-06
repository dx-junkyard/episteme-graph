"""Integration tests for ComponentGraphAgent with mocked LLM."""
from __future__ import annotations

import json
from unittest.mock import patch

from episteme_graph.agents.component_assembly.schema import ComponentAssemblyResult, ComponentRecord
from episteme_graph.agents.component_graph.agent import ComponentGraphAgent
from episteme_graph.agents.component_graph.schema import ComponentGraphResult


def _make_component(
    cid: str,
    label: str,
    linked_dsl_node_ids: list[str] | None = None,
) -> ComponentRecord:
    return ComponentRecord(
        component_id=cid,
        component_type="TheoryComponent",
        label=label,
        summary=f"Summary of {label}",
        inputs=[],
        outputs=[],
        preconditions=[],
        cautions=[],
        dependencies=[],
        evidence_refs={},
        reason="test",
        confidence=0.9,
        review_notes=[],
        linked_dsl_node_ids=linked_dsl_node_ids or [],
    )


def _make_assembly(components: list[ComponentRecord]) -> ComponentAssemblyResult:
    return ComponentAssemblyResult(
        document_id="doc_test",
        components_version="v1",
        cartridge_id=None,
        components=components,
        assembly_hints=[],
        review_notes=[],
        confidence=0.9,
    )


_VALID_LLM_RESPONSE = {
    "edges": [
        {
            "edge_id": "component_edge_0001",
            "source": "comp_001",
            "target": "comp_002",
            "edge_type": "REQUIRES",
            "support_status": "llm_inferred",
            "evidence_claims": ["claim:b1:s1"],
            "reasoning": "Comp 002 requires comp 001.",
            "confidence": 0.85,
        }
    ]
}


class TestComponentGraphAgentRun:
    def setup_method(self):
        self.agent = ComponentGraphAgent()
        self.components = _make_assembly([
            _make_component("comp_001", "Component A"),
            _make_component("comp_002", "Component B"),
        ])

    def test_run_with_valid_llm_response(self):
        with patch.object(
            self.agent._llm_client, "generate", return_value=_VALID_LLM_RESPONSE
        ):
            result = self.agent.run(self.components)
        assert isinstance(result, ComponentGraphResult)
        assert result.document_id == "doc_test"
        assert len(result.nodes) == 2
        assert len(result.edges) == 1
        assert result.edges[0].source == "comp_001"
        assert result.edges[0].target == "comp_002"

    def test_run_returns_fallback_on_llm_error(self):
        with patch.object(
            self.agent._llm_client, "generate", side_effect=RuntimeError("LLM unavailable")
        ):
            result = self.agent.run(self.components)
        assert isinstance(result, ComponentGraphResult)
        assert result.edges == []
        assert any(i.severity == "error" for i in result.validation_issues)

    def test_run_with_no_components(self):
        empty_assembly = _make_assembly([])
        result = self.agent.run(empty_assembly)
        assert isinstance(result, ComponentGraphResult)
        assert result.edges == []

    def test_run_repair_on_invalid_edge(self):
        bad_response = {
            "edges": [
                {
                    "edge_id": "component_edge_0001",
                    "source": "NONEXISTENT",
                    "target": "comp_002",
                    "edge_type": "REQUIRES",
                    "support_status": "llm_inferred",
                    "evidence_claims": [],
                    "reasoning": "",
                    "confidence": 0.8,
                }
            ]
        }
        fixed_response = {
            "edges": [
                {
                    "edge_id": "component_edge_0001",
                    "source": "comp_001",
                    "target": "comp_002",
                    "edge_type": "REQUIRES",
                    "support_status": "llm_inferred",
                    "evidence_claims": [],
                    "reasoning": "repaired",
                    "confidence": 0.8,
                }
            ]
        }
        call_count = {"n": 0}

        def mock_generate(messages):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return bad_response
            return fixed_response

        with patch.object(self.agent._llm_client, "generate", side_effect=mock_generate):
            result = self.agent.run(self.components)
        assert result.edges[0].source == "comp_001"

    def test_run_with_existing_graph_merges(self):
        existing_graph = {
            "edges": [
                {
                    "edge_id": "old_edge",
                    "source_component_id": "comp_001",
                    "target_component_id": "comp_002",
                    "relation": "ENABLES",
                    "support_status": "io_matched",
                    "confidence": 0.6,
                    "evidence": {},
                }
            ]
        }
        with patch.object(
            self.agent._llm_client, "generate", return_value=_VALID_LLM_RESPONSE
        ):
            result = self.agent.run(self.components, existing_graph=existing_graph)

        # Both ENABLES (existing) and REQUIRES (LLM) edges should be present
        keys = {(e.source, e.target, e.edge_type) for e in result.edges}
        assert ("comp_001", "comp_002", "REQUIRES") in keys
        assert ("comp_001", "comp_002", "ENABLES") in keys

    def test_run_to_graph_payload(self):
        with patch.object(
            self.agent._llm_client, "generate", return_value=_VALID_LLM_RESPONSE
        ):
            result = self.agent.run(self.components)
        payload = result.to_graph_payload()
        assert "nodes" in payload
        assert "edges" in payload
        assert payload["edges"][0]["source_component_id"] == "comp_001"
        assert "evidence" in payload["edges"][0]
