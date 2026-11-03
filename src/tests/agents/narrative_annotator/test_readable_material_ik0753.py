"""IK-0753: NarrativeAnnotator's material for main-graph nodes is human-readable.

The derivation takeaways the aggregation keeps (``Derive eq_… from eq_… via a -> b``
/ ``Logical progression through N claims in section sec_X``) are machine sentences;
the prompt material is built from labels, descriptions, the claim text of the
aggregated steps and printed equation numbers, with nodes and edges addressed by
handles. No internal-ID token (the display projection's pattern) reaches the prompt.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from episteme_graph.agents.component_graph.schema import (
    ComponentGraphEdge,
    ComponentGraphNode,
    ComponentGraphResult,
    GRAPH_SCHEMA_VERSION,
)
from episteme_graph.agents.internal_id_text import INTERNAL_ID_RE
from episteme_graph.agents.narrative_annotator.agent import NarrativeAnnotator
from episteme_graph.agents.narrative_annotator.input_builder import (
    NarrativeInputBuilder,
    human_text,
    printed_equation_label,
)
from episteme_graph.agents.narrative_annotator.prompt import NarrativePromptFactory


def _graph():
    basis = ComponentGraphNode(
        component_id="theory_op_0001", label="Theory basis",
        component_type="TheoryOperationNode", graph_layer="main",
        description="The binary is modelled in an AdS background (tex_b80).",
        linked_derivation_ids=["derivation_eq_tex_b16", "derivation_claim_sec_1"],
        linked_equation_ids=["eq_12", "eq_2_7", "eq_eqcand_inline_tex_b36_80_e2081ccd"],
        linked_claim_ids=["claim_span_3", "synth_claim_0001"],
        member_component_ids=["eq_op_0001"],
        display_order=0,
    )
    relation = ComponentGraphNode(
        component_id="theory_op_0002", label="Consistency relation",
        component_type="TheoryOperationNode", graph_layer="main",
        description="", display_order=1,
    )
    detail = ComponentGraphNode(
        component_id="eq_op_0001", label="Derive eq_tex_b78",
        component_type="EquationOperationNode", graph_layer="equation_detail",
        parent_component_id="theory_op_0001",
        description="The 2PN energy follows from the conformal algebra.",
    )
    detail_machine = ComponentGraphNode(
        component_id="eq_op_0002", label="Derive",
        component_type="EquationOperationNode", graph_layer="equation_detail",
        parent_component_id="theory_op_0001",
        description="Reusable theory unit (derive_consequence); covers 5 equation step(s).",
    )
    edge = ComponentGraphEdge(
        edge_id="edge_0001", source="theory_op_0001", target="theory_op_0002",
        edge_type="derives", support_status="source_inferred",
        evidence_claims=[], reasoning="Basis feeds eq_tex_b78 into the relation",
    )
    return ComponentGraphResult(
        document_id="doc", graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None, nodes=[basis, relation, detail, detail_machine],
        edges=[edge], review_notes=[], confidence=0.8,
    )


class _Chain:
    def __init__(self, derivation_id, takeaway):
        self.derivation_id = derivation_id
        self.teaching_takeaway = takeaway


class _Derivations:
    chains = [
        _Chain("derivation_eq_tex_b16",
               "Derive eq_post_laplace from eq_eqcand_inline_tex_b36_80_e2081ccd via approximate -> derive_result"),
        _Chain("derivation_claim_sec_1", "Logical progression through 27 claims in section sec_1"),
    ]


class _Thesis:
    central_thesis = {"text": "Conformal symmetry fixes the 2PN dynamics."}


def _prompt(llm_input) -> str:
    messages = NarrativePromptFactory().build_messages(llm_input)
    return messages[0]["content"] + "\n" + messages[1]["content"]


def test_prompt_material_has_no_internal_id_tokens():
    llm_input = NarrativeInputBuilder().build(_graph(), thesis=_Thesis(), derivations=_Derivations())
    content = _prompt(llm_input)
    assert not INTERNAL_ID_RE.findall(content), INTERNAL_ID_RE.findall(content)
    for internal in ("theory_op_0001", "edge_0001", "claim_span_3", "sec_1", "doc"):
        assert f'"{internal}"' not in content


def test_machine_takeaways_and_counts_are_not_material():
    llm_input = NarrativeInputBuilder().build(_graph(), derivations=_Derivations())
    content = _prompt(llm_input)
    for machine in ("Logical progression", "->", "step(s)", "27 claims", "Reusable theory unit"):
        assert machine not in json.dumps(llm_input.main_nodes, ensure_ascii=False)
    assert "Logical progression" not in content


def test_node_material_is_built_from_readable_sources():
    llm_input = NarrativeInputBuilder().build(_graph(), derivations=_Derivations())
    basis = next(n for n in llm_input.main_nodes if n["ref"] == "N1")
    assert basis["label"] == "Theory basis"
    assert basis["description"] == "The binary is modelled in an AdS background."
    assert basis["teaching_takeaways"] == [
        "The 2PN energy follows from the conformal algebra."
    ]
    # Printed equation numbers are enumerated; internal equation IDs are dropped.
    assert basis["equations"] == ["(12)", "(2.7)"]
    assert "linked_claim_ids" not in basis and "linked_equation_ids" not in basis


def test_llm_answers_by_ref_and_results_keep_internal_ids():
    graph = _graph()
    output = {
        "graph_summary": "Conformal symmetry fixes the dynamics.",
        "node_narratives": [
            {"ref": "N1", "narrative_role": "Sets up the model.", "reason": "d", "confidence": 0.8},
            {"ref": "N2", "narrative_role": "States the relation.", "reason": "d", "confidence": 0.8},
        ],
        "edge_narratives": [
            {"ref": "E1", "transition_text": "Hands the model on.", "reason": "e", "confidence": 0.7},
        ],
        "review_notes": [],
        "confidence": 0.8,
    }
    agent = NarrativeAnnotator()
    with patch.object(agent._llm_client, "generate", return_value=output):
        result = agent.run(graph, thesis=_Thesis(), derivations=_Derivations())
    assert {n.component_id for n in result.node_narratives} == {"theory_op_0001", "theory_op_0002"}
    assert [e.edge_id for e in result.edge_narratives] == ["edge_0001"]
    assert not [i for i in result.validation_issues if i.severity == "error"]


def test_repair_messages_show_refs_not_internal_ids():
    graph = _graph()
    bad = {"graph_summary": "s", "node_narratives": [], "edge_narratives": [],
           "review_notes": [], "confidence": 0.5}
    llm_input = NarrativeInputBuilder().build(graph)
    from episteme_graph.agents.narrative_annotator.repair import _parse_raw
    from episteme_graph.agents.narrative_annotator.validator import NarrativeValidator
    issues = NarrativeValidator().validate(_parse_raw(bad, llm_input), llm_input)
    messages = NarrativePromptFactory().build_repair_messages(llm_input, bad, issues)
    issue_text = messages[1]["content"].split("## Validation Issues", 1)[1]
    assert "theory_op_0001" not in issue_text
    assert "N1" in issue_text


def test_helpers():
    assert printed_equation_label("eq_12") == "(12)"
    assert printed_equation_label("eq_2_7") == "(2.7)"
    assert printed_equation_label("eq_tex_b78") == ""
    assert human_text("Define: Reusable theory unit (define_relation); covers 1 equation step(s).") == ""
    assert human_text("A claim about [tex_b80, tex_b81] matter.") == "A claim about matter."
