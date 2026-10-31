"""major_equations の選び方（先頭切り捨てをやめる・J-3）。

実測（uxsim radar_loop の thesis 要求 seq 1958/1962/1966/2035/2039）では、
式の多い論文で major_equations が前半の 16 式で頭打ちになり、結果節の式
（主結果・推定量・共分散）が中心命題の材料に入らなかった。
"""
from types import SimpleNamespace

from episteme_graph.agents.equation_selection import select_equation_indices
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult
from episteme_graph.agents.paper_skeleton.schema import LogicalBlock
from episteme_graph.agents.thesis_reconstruction.input_builder import (
    ThesisReconstructionInputBuilder,
)

from .test_input_builder import _make_equation_record, _qualified, _skeleton

BUILDER = ThesisReconstructionInputBuilder()


def _many_equations(per_section=10, sections=4):
    records = []
    for s in range(1, sections + 1):
        for k in range(per_section):
            rec = _make_equation_record(
                equation_id=f"eq_{s}_{k}", block_id=f"e{s}_{k}", section_id=f"sec_{s}",
            )
            rec.semantics.equation_type = "relation"
            rec.semantics.role_in_argument = "derived"
            records.append(rec)
    return EquationSemanticsResult("doc", None, [], records)


def _skeleton_with_result_section():
    skel = _skeleton()
    skel.logical_blocks = list(skel.logical_blocks) + [
        LogicalBlock("l9", "result", "Results", ["sec_4"], [], "main result", "r", 0.9)
    ]
    return skel


def test_result_section_and_claim_linked_equations_are_not_truncated():
    claim_objects = SimpleNamespace(claims=[
        SimpleNamespace(claim_id="claim:b2:s1", equation_ids=["eq_3_5"]),
    ])
    llm_input = BUILDER.build(
        _skeleton_with_result_section(), _qualified(), _many_equations(),
        claim_objects=claim_objects,
    )
    ids = [e["equation_id"] for e in llm_input.major_equations]
    assert len(ids) == 16
    assert "eq_3_5" in ids  # 主張が参照する式
    assert {f"eq_4_{k}" for k in range(10)} <= set(ids)  # 結果節は丸ごと
    # 残り枠は前半の節に層化して配られる（先頭の節だけで埋まらない）
    assert any(i.startswith("eq_1_") for i in ids)
    assert any(i.startswith("eq_2_") for i in ids)
    # 文書順を保つ
    order = [r.equation_id for r in _many_equations().equations]
    assert ids == sorted(ids, key=order.index)


def test_without_priorities_the_last_section_is_still_represented():
    sel = select_equation_indices(_many_equations().equations, 16)
    sections = {_many_equations().equations[i].source_extraction.source_location["section_id"] for i in sel.indices}
    assert sections == {"sec_1", "sec_2", "sec_3", "sec_4"}
    assert sel.truncated
    assert sel.unprocessed_sections  # 取りこぼしの節を列挙する


def test_synthesized_claims_do_not_promote_equations():
    claim_objects = SimpleNamespace(claims=[
        SimpleNamespace(claim_id="synth_claim_0001", equation_ids=["eq_1_0"]),
    ])
    llm_input = BUILDER.build(
        _skeleton(), _qualified(), _many_equations(), claim_objects=claim_objects,
    )
    assert len(llm_input.major_equations) == 16


def test_no_limit_or_small_population_keeps_all():
    eqs = _many_equations(per_section=2, sections=2).equations
    assert select_equation_indices(eqs, 16).indices == list(range(4))
    assert select_equation_indices(_many_equations().equations, 0).indices == list(range(40))


def test_coverage_reports_equation_truncation_and_rule():
    facts = BUILDER.compute_input_coverage(
        _skeleton_with_result_section(), _qualified(), _many_equations(),
    )
    assert "max_equations" in facts["reasons"]
    assert facts["details"]["equation_selection"] == "linked_and_result_first_then_stratified"
    assert facts["details"]["unprocessed_equation_sections"]
