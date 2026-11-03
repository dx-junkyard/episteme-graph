"""式の chain と claim の chain を併走させる（2026-09-19）。

以前は「導出に使える式が1本でもあれば claim chain を作らない」だったため、式が数本
だけ生き残った論文では本文の論理の大半が chain から落ちていた（逆に全式が使えない
論文では claim chain だけになり、その事実がどこにも残らなかった）。

固定する契約:

① 式の chain が触れていない節は claim chain が補う
② 式の chain が覆っている節は claim chain でなぞり直さない（同じ論理の二重化を防ぐ）
③ 「式のうち何本を導出に使えたか」を ``summary_stats.coverage``（共通形式）に残し、
   1本も使えなかったときは ``validation_issues`` にも理由付きで残す
"""
from __future__ import annotations

import types

from episteme_graph.agents.coverage_report import is_coverage_report
from episteme_graph.agents.derivation_chain import DerivationChainAgent

from tests.agents.derivation_chain.test_agent import (
    _block_derivation_use,
    _make_eq,
    _make_result,
)


def _claim(claim_id: str, claim_type: str, section_id: str):
    return types.SimpleNamespace(
        claim_id=claim_id,
        claim_type=claim_type,
        section_id=section_id,
        source_evidence_ids=[],
        confidence=0.6,
        review_note="",
    )


def _claims(*claims):
    return types.SimpleNamespace(claims=list(claims))


def _rule_ids(result) -> set[str]:
    return {issue.rule_id for issue in result.validation_issues}


# ---------------------------------------------------------------------------
# ① / ② 併走と重複回避
# ---------------------------------------------------------------------------


def test_claim_chains_complement_sections_without_equation_chains():
    equations = _make_result([
        _make_eq("eq_1", section_id="doc_test:sec_2"),
        _make_eq("eq_2", from_eqs=["eq_1"], role="result", section_id="doc_test:sec_2"),
    ])
    claims = _claims(
        _claim("claim_a", "assumption", "doc_test:sec_5"),
        _claim("claim_b", "result", "doc_test:sec_5"),
    )
    result = DerivationChainAgent().run(equations, claim_build_result=claims)

    kinds = {c.chain_type for c in result.chains}
    assert "equation_chain" in kinds
    assert "claim_chain" in kinds
    claim_chains = [c for c in result.chains if c.chain_type == "claim_chain"]
    assert [c.source_section_ids for c in claim_chains] == [["doc_test:sec_5"]]
    assert "derivation_claim_chain_complement" in _rule_ids(result)


def test_sections_covered_by_equation_chains_are_not_duplicated():
    equations = _make_result([
        _make_eq("eq_1", section_id="doc_test:sec_2"),
        _make_eq("eq_2", from_eqs=["eq_1"], role="result", section_id="doc_test:sec_2"),
    ])
    claims = _claims(
        _claim("claim_a", "assumption", "doc_test:sec_2"),
        _claim("claim_b", "result", "doc_test:sec_2"),
    )
    result = DerivationChainAgent().run(equations, claim_build_result=claims)

    assert [c.chain_type for c in result.chains] == ["equation_chain"]
    assert "derivation_claim_chain_complement" not in _rule_ids(result)


def test_sections_stay_covered_by_claims_when_the_equation_chain_breaks():
    """使える式が残っていても chain にならなければ、その節は claim が補う。"""
    equations = _make_result([
        _make_eq("eq_1", section_id="doc_test:sec_2"),
        _block_derivation_use(
            _make_eq("eq_2", from_eqs=["eq_1"], role="result", section_id="doc_test:sec_2")
        ),
    ])
    claims = _claims(
        _claim("claim_a", "assumption", "doc_test:sec_2"),
        _claim("claim_b", "result", "doc_test:sec_2"),
    )
    result = DerivationChainAgent().run(equations, claim_build_result=claims)

    # eq_1 は単独で（入力リンクを持たないので）chain にならない。
    assert [c.chain_type for c in result.chains] == ["claim_chain"]
    assert "derivation_claim_chain_complement" in _rule_ids(result)
    assert "derivation_excludes_inconsistent_equation" in _rule_ids(result)


def test_claim_chains_stand_alone_when_every_equation_is_blocked():
    equations = _make_result([
        _block_derivation_use(_make_eq("eq_1", section_id="doc_test:sec_2")),
        _block_derivation_use(
            _make_eq("eq_2", from_eqs=["eq_1"], role="result", section_id="doc_test:sec_2")
        ),
    ])
    claims = _claims(
        _claim("claim_a", "assumption", "doc_test:sec_2"),
        _claim("claim_b", "result", "doc_test:sec_2"),
    )
    result = DerivationChainAgent().run(equations, claim_build_result=claims)

    assert [c.chain_type for c in result.chains] == ["claim_chain"]
    assert "derivation_equation_only_fallback" in _rule_ids(result)
    assert "derivation_all_equations_blocked" in _rule_ids(result)


# ---------------------------------------------------------------------------
# ③ 取りこぼしの報告
# ---------------------------------------------------------------------------


def test_summary_stats_reports_equation_coverage():
    equations = _make_result([
        _make_eq("eq_1"),
        _block_derivation_use(_make_eq("eq_2", from_eqs=["eq_1"], role="result")),
    ])
    result = DerivationChainAgent().run(equations)
    coverage = result.summary_stats["coverage"]

    assert is_coverage_report(coverage)
    assert coverage["population"] == 2
    assert coverage["processed"] == 1
    assert coverage["truncated"] == 1
    assert coverage["reasons"] == ["equation_confidence_policy"]
    assert coverage["details"]["blocked_equation_ids"] == ["eq_2"]


def test_all_equations_blocked_is_stated_as_a_reason():
    equations = _make_result([
        _block_derivation_use(_make_eq("eq_1")),
        _block_derivation_use(_make_eq("eq_2", from_eqs=["eq_1"], role="result")),
    ])
    result = DerivationChainAgent().run(equations)

    assert "derivation_all_equations_blocked" in _rule_ids(result)
    coverage = result.summary_stats["coverage"]
    assert coverage["processed"] == 0
    assert coverage["truncated"] == coverage["population"] == 2


def test_no_equations_at_all_does_not_claim_a_blocked_reason():
    result = DerivationChainAgent().run(_make_result([]))
    assert "derivation_all_equations_blocked" not in _rule_ids(result)
    assert "derivation_no_equations" in _rule_ids(result)
    assert result.summary_stats["coverage"]["population"] == 0


def test_steps_backed_by_reconstructed_equations_say_so():
    """復元由来の式に支えられた step は、そう分かる形で残る（強い根拠にしない）。"""
    from episteme_graph.agents.derivation_chain.agent import RECONSTRUCTION_BACKED_REASON

    equations = _make_result([
        _make_eq("eq_1"),
        _make_eq("eq_2", from_eqs=["eq_1"], role="result"),
    ])
    for record in equations.equations:
        record.confidence_policy.must_not_treat_as_source_extracted = True
    result = DerivationChainAgent().run(equations)
    steps = [s for chain in result.chains for s in chain.steps]
    assert steps
    assert all(s.review_reason == RECONSTRUCTION_BACKED_REASON for s in steps)


def test_steps_backed_by_extracted_equations_carry_no_reason():
    equations = _make_result([
        _make_eq("eq_1"),
        _make_eq("eq_2", from_eqs=["eq_1"], role="result"),
    ])
    result = DerivationChainAgent().run(equations)
    steps = [s for chain in result.chains for s in chain.steps]
    assert steps
    assert all(s.review_reason == "" for s in steps)


def test_summary_stats_survive_the_artifact_round_trip():
    from episteme_graph.agents.derivation_chain.schema import DerivationChainResult

    equations = _make_result([
        _make_eq("eq_1"),
        _block_derivation_use(_make_eq("eq_2", from_eqs=["eq_1"], role="result")),
    ])
    result = DerivationChainAgent().run(equations)
    restored = DerivationChainResult.from_dict(result.to_dict())
    assert restored.summary_stats == result.summary_stats
