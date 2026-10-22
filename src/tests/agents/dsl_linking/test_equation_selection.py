"""dsl_linking の式入力も先頭切り捨てをしない（thesis が参照する式を優先・J-3）。"""
from types import SimpleNamespace

from episteme_graph.agents.dsl_linking.input_builder import DSLLinkingInputBuilder

from tests.agents.thesis_reconstruction.test_equation_selection import _many_equations


def test_thesis_referenced_equations_are_kept_and_sections_spread():
    thesis = SimpleNamespace(
        central_thesis={"text": "t", "equation_ids": ["eq_4_9"], "claim_ids": []},
        support_structure={"direct_supports": [{"equation_ids": ["eq_3_7"], "claim_ids": []}]},
        excluded_from_core=[],
        confidence=0.8,
    )
    builder = DSLLinkingInputBuilder()
    eqs = builder._equations(
        _many_equations(sections=5), 24,
        linked_equation_ids={"eq_4_9", "eq_3_7"},
    )
    ids = [e["equation_id"] for e in eqs]
    assert len(ids) == 24
    assert {"eq_4_9", "eq_3_7"} <= set(ids)
    assert any(i.startswith("eq_5_") for i in ids)
    from episteme_graph.agents.dsl_linking.input_builder import _dsl_linked_equation_ids
    assert _dsl_linked_equation_ids([], None, thesis) == {"eq_4_9", "eq_3_7"}
