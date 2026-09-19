"""Tests for deterministic concept enrichment (issue #8).

P0-2 / P0-3（`docs/architecture/knowledge_structure_review_2026-09-12.md` §4）の
回帰テストを末尾にまとめてある。
"""
from episteme_graph.agents.claim_object_builder.schema import ClaimConcept
from episteme_graph.agents.component_assembly.enrichment import enrich_component_assembly
from episteme_graph.agents.component_assembly.schema import (
    COMPONENTS_VERSION,
    ComponentAssemblyLLMInput,
    ComponentAssemblyResult,
    ComponentRecord,
    concept_name_list,
)


def _component(
    component_id,
    *,
    support_distance,
    linked_claim_ids,
    linked_equation_ids=None,
    preconditions=None,
):
    return ComponentRecord(
        component_id,
        "RelationComponent",
        f"{component_id} label",
        f"{component_id} summary",
        [], [], list(preconditions or []), [], [],
        {"claim_ids": [], "equation_ids": list(linked_equation_ids or []),
         "thesis_refs": [], "dsl_refs": {"node_ids": [], "edge_ids": []}},
        "reason",
        0.8,
        [],
        linked_claim_ids=list(linked_claim_ids),
        linked_equation_ids=list(linked_equation_ids or []),
        support_distance_to_headline_claim=support_distance,
    )


def _llm_input():
    return ComponentAssemblyLLMInput(
        document_id="doc",
        cartridge_id=None,
        accepted_claims=[],
        equations=[
            {"equation_id": "eq_1", "defined_symbols": [{"symbol": "b_1"}], "used_symbols": []},
        ],
        thesis_nodes=[],
        dsl_nodes=[],
        dsl_edges=[],
        allowed_component_types=["RelationComponent"],
        allowed_dependency_types=["requires"],
        available_claims=[
            {"claim_id": "claim_1", "atomicity": "atomic", "is_atomic": True,
             "concepts": ["Skewness", "Kurtosis"]},
            {"claim_id": "claim_2", "atomicity": "atomic", "is_atomic": True,
             "concepts": ["Skewness", "Galaxy bias"]},
            {"claim_id": "claim_composite", "atomicity": "composite", "is_atomic": False,
             "concepts": ["Should be ignored"]},
        ],
    )


def test_enrichment_fills_concepts_from_atomic_claims():
    comp_a = _component("comp_a", support_distance=0, linked_claim_ids=["claim_1"], linked_equation_ids=["eq_1"])
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [comp_a], [], [], 0.8)

    enrich_component_assembly(result, _llm_input())

    concepts = result.components[0].concepts
    assert "Skewness" in concepts
    assert "Kurtosis" in concepts
    # P0-3: 式の記号（eq_1 の defined_symbol "b_1"）は concepts に入らない。
    # 記号の正本は symbol_registry で、component からは linked_equation_ids
    # 経由で辿れるため情報は失われない（F-6 / K-2）。
    assert "b_1" not in concepts


def test_enrichment_ignores_non_atomic_claim_concepts():
    comp = _component("comp_x", support_distance=0, linked_claim_ids=["claim_composite"])
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [comp], [], [], 0.8)

    enrich_component_assembly(result, _llm_input())

    assert "Should be ignored" not in result.components[0].concepts


def test_enrichment_splits_introduced_and_reused_by_support_order():
    comp_a = _component("comp_a", support_distance=0, linked_claim_ids=["claim_1"])
    comp_b = _component("comp_b", support_distance=1, linked_claim_ids=["claim_2"])
    # Provide them out of support order to confirm ordering is by distance.
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [comp_b, comp_a], [], [], 0.8)

    enrich_component_assembly(result, _llm_input())

    by_id = {c.component_id: c for c in result.components}
    assert "Skewness" in by_id["comp_a"].introduced_concepts
    assert "Kurtosis" in by_id["comp_a"].introduced_concepts
    # comp_b is farther from the headline; Skewness was already introduced by comp_a.
    assert "Skewness" in by_id["comp_b"].reused_concepts
    assert "Galaxy bias" in by_id["comp_b"].introduced_concepts


def _llm_input_with_terms():
    """Same claims/equations as _llm_input() but with a cartridge term vocabulary."""
    inp = _llm_input()
    inp.normalized_terms = [
        {"canonical": "Galaxy bias", "aliases": ["galaxy bias", "nonlinear bias"]},
    ]
    return inp


def test_enrichment_fills_prerequisite_concepts_from_preconditions():
    # Precondition text is matched against the normalized-term vocabulary to find
    # grounded prerequisite concepts (issue #8).
    comp = _component(
        "comp_a",
        support_distance=0,
        linked_claim_ids=["claim_1"],
        preconditions=[{"condition": "assumes a nonlinear bias model holds"}],
    )
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [comp], [], [], 0.8)

    enrich_component_assembly(result, _llm_input_with_terms())

    assert result.components[0].prerequisite_concepts == ["Galaxy bias"]


def test_enrichment_dedups_concepts_across_sources():
    # claim_1 carries "Skewness" twice (claim_1 + symbol overlap is not needed):
    # two atomic claims both contributing Skewness must not duplicate it.
    comp = _component(
        "comp_a",
        support_distance=0,
        linked_claim_ids=["claim_1", "claim_2"],
    )
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [comp], [], [], 0.8)

    enrich_component_assembly(result, _llm_input())

    concepts = result.components[0].concepts
    assert concepts.count("Skewness") == 1


def test_enrichment_without_support_order_falls_back_safely():
    # No support_distance differences (all 0) -> ordering falls back to component
    # order without raising; concepts still split into introduced/reused.
    comp_a = _component("comp_a", support_distance=0, linked_claim_ids=["claim_1"])
    comp_b = _component("comp_b", support_distance=0, linked_claim_ids=["claim_2"])
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [comp_a, comp_b], [], [], 0.8)

    enrich_component_assembly(result, _llm_input())

    by_id = {c.component_id: c for c in result.components}
    # comp_a appears first -> introduces Skewness; comp_b reuses it.
    assert "Skewness" in by_id["comp_a"].introduced_concepts
    assert "Skewness" in by_id["comp_b"].reused_concepts


def test_enrichment_component_without_claims_or_equations_is_empty_but_safe():
    comp = _component("comp_a", support_distance=0, linked_claim_ids=[])
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [comp], [], [], 0.8)

    enrich_component_assembly(result, _llm_input())

    c = result.components[0]
    assert c.concepts == []
    assert c.introduced_concepts == []
    assert c.reused_concepts == []
    assert c.prerequisite_concepts == []


# ---------------------------------------------------------------------------
# P0-2 / P0-3（knowledge_structure_review_2026-09-12 §4 Phase 0）
# ---------------------------------------------------------------------------


def _component_with_text(component_id, *, summary, **kwargs):
    comp = _component(component_id, **kwargs)
    comp.summary = summary
    return comp


def _input_with_terms(terms, *, claims=None):
    inp = _llm_input()
    inp.normalized_terms = list(terms)
    if claims is not None:
        inp.available_claims = list(claims)
    return inp


def _enrich_one(component, llm_input):
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [component], [], [], 0.8)
    enrich_component_assembly(result, llm_input)
    return result.components[0]


_SHORT_ALIAS_TERMS = [{"canonical": "Standard Model", "aliases": ["SM"]}]


def test_short_alias_does_not_match_inside_unrelated_word():
    # F-7 / K-3: alias "SM" が "cosmological" に部分一致し、原本に 0 回の
    # "Standard Model" が 28 ノード中 25 に注入されていた回帰。
    comp = _component_with_text(
        "comp_a",
        summary="cosmological perturbations with a mismatch in the bias model",
        support_distance=0,
        linked_claim_ids=[],
        preconditions=[{"condition": "assumes cosmological parameters are fixed"}],
    )

    enriched = _enrich_one(comp, _input_with_terms(_SHORT_ALIAS_TERMS))

    assert "Standard Model" not in enriched.concepts
    assert "Standard Model" not in enriched.prerequisite_concepts


def test_short_alias_matches_as_a_standalone_word():
    # 語として現れるときは従来どおり当たる（情報を落とさない）。
    comp = _component_with_text(
        "comp_a",
        summary="compares the SM prediction with the measured rate",
        support_distance=0,
        linked_claim_ids=[],
    )

    assert "Standard Model" in _enrich_one(comp, _input_with_terms(_SHORT_ALIAS_TERMS)).concepts


def test_long_alias_still_matches_case_insensitively_on_word_boundary():
    # 通常の概念名は従来どおり（大小無視・語境界つき）。
    terms = [{"canonical": "Galaxy bias", "aliases": ["galaxy bias"]}]
    comp = _component_with_text(
        "comp_a",
        summary="The Galaxy Bias model is calibrated here",
        support_distance=0,
        linked_claim_ids=[],
    )

    assert "Galaxy bias" in _enrich_one(comp, _input_with_terms(terms)).concepts


def test_claim_concepts_given_as_a_string_are_not_split_into_characters():
    # F0-6: concepts が str のとき list() が 1 文字ずつに割り、
    # prerequisite_concepts が ['r','a','p','i','s','t'] になっていた回帰。
    claims = [{"claim_id": "claim_str", "atomicity": "atomic", "is_atomic": True,
               "concepts": "raptis"}]
    comp = _component("comp_a", support_distance=0, linked_claim_ids=["claim_str"])

    enriched = _enrich_one(comp, _input_with_terms([], claims=claims))

    assert enriched.concepts == ["raptis"]


def test_equation_symbols_are_not_mixed_into_concepts():
    # P0-3: 記号層は symbol_registry に閉じる（F-6 / K-2）。
    comp = _component(
        "comp_a",
        support_distance=0,
        linked_claim_ids=["claim_1"],
        linked_equation_ids=["eq_sym"],
    )
    inp = _llm_input()
    inp.equations = [{
        "equation_id": "eq_sym",
        "defined_symbols": [{"symbol": "R"}, {"symbol": "\\lambda"}],
        "used_symbols": ["\\phi", "theta"],
    }]

    enriched = _enrich_one(comp, inp)

    assert "R" not in enriched.concepts
    assert "\\lambda" not in enriched.concepts
    assert "\\phi" not in enriched.concepts
    # 概念（claim 由来）は残る。
    assert "Skewness" in enriched.concepts


def test_symbol_typed_claim_concepts_are_excluded():
    # equation_claim_synthesis の claim concept は全件 concept_type="symbol"。
    claims = [{
        "claim_id": "claim_sym",
        "atomicity": "atomic",
        "is_atomic": True,
        "concepts": [
            {"name": "lambda", "normalized": "lambda", "concept_type": "symbol"},
            {"name": "galaxy bias", "normalized": "Galaxy bias", "concept_type": "observable"},
        ],
    }]
    comp = _component("comp_a", support_distance=0, linked_claim_ids=["claim_sym"])

    enriched = _enrich_one(comp, _input_with_terms([], claims=claims))

    assert enriched.concepts == ["Galaxy bias"]


def test_two_character_concept_names_are_excluded():
    # 型が読めない str 形でも記号を落とせる唯一の手掛かりが長さ（MIN_CONCEPT_NAME_LENGTH）。
    claims = [{"claim_id": "claim_short", "atomicity": "atomic", "is_atomic": True,
               "concepts": ["R", "b1", "Skewness"]}]
    comp = _component("comp_a", support_distance=0, linked_claim_ids=["claim_short"])

    enriched = _enrich_one(comp, _input_with_terms([], claims=claims))

    assert enriched.concepts == ["Skewness"]


def test_concept_name_list_normalizes_every_shape():
    # 型契約の正本（str は 1 要素 / dict・オブジェクト形は normalized 優先 /
    # concept_type="symbol" と 2 文字以下は除外 / 重複は 1 回）。
    assert concept_name_list(None) == []
    assert concept_name_list("raptis") == ["raptis"]
    assert concept_name_list(["Skewness", "Skewness"]) == ["Skewness"]
    assert concept_name_list([{"name": "skewness", "normalized": "Skewness"}]) == ["Skewness"]
    assert concept_name_list([
        {"name": "lambda", "normalized": "lambda", "concept_type": "symbol"},
    ]) == []
    assert concept_name_list(["R", "b1", "abc"]) == ["abc"]
    assert concept_name_list([
        ClaimConcept(name="gravity", normalized="Gravity", concept_type="theory"),
        ClaimConcept(name="R", normalized="R", concept_type="symbol"),
    ]) == ["Gravity"]


def test_component_record_from_dict_does_not_split_a_string_into_characters():
    # F0-6 の入口のひとつ: list("raptis") で 1 文字ずつになる復元経路。
    restored = ComponentAssemblyResult.from_dict({
        "document_id": "doc",
        "components_version": COMPONENTS_VERSION,
        "components": [{
            "component_id": "comp_a",
            "component_type": "RelationComponent",
            "label": "label",
            "summary": "summary",
            "concepts": "raptis",
            "prerequisite_concepts": "raptis",
        }],
    })

    assert restored.components[0].concepts == ["raptis"]
    assert restored.components[0].prerequisite_concepts == ["raptis"]


def test_low_confidence_equation_flips_component_review_status():
    # An equation needing math review is linked; the component review_status must
    # escalate from auto_accepted to teacher_review_required (issue #8).
    comp = _component(
        "comp_a",
        support_distance=0,
        linked_claim_ids=["claim_1"],
        linked_equation_ids=["eq_low"],
    )
    comp.review_status = "auto_accepted"
    inp = _llm_input()
    inp.equations = [
        {"equation_id": "eq_low", "defined_symbols": [{"symbol": "b_2"}],
         "used_symbols": [], "needs_math_review": True},
    ]
    result = ComponentAssemblyResult("doc", COMPONENTS_VERSION, None, [comp], [], [], 0.8)

    enrich_component_assembly(result, inp)

    assert "eq_low" in result.components[0].review_required_equation_ids
    assert result.components[0].review_status == "teacher_review_required"


def test_concept_name_list_symbol_format_rules():
    """記号かどうかは書式で判定する（分野語をハードコードしない）。

    ASCII の短いトークン・LaTeX 制御記法・添字記法は記号層へ落とし、非 ASCII の
    概念名は 2 文字から残す（「重力」を巻き込まない）。
    """
    # 落ちる: ASCII 1〜2 文字 / 非 ASCII 1 文字 / LaTeX 制御記法 / 添字記法
    for name in ("R", "e", "b1", "λ", "\\lambda", "$R$", "{x}", "b_1", "R_D", "x^2"):
        assert concept_name_list([name]) == [], name
    # 残る: 2 文字以上の非 ASCII 概念名 / 通常の語 / snake_case の概念名
    for name in ("重力", "Galaxy bias", "abc", "zero_recoil_limit"):
        assert concept_name_list([name]) == [name], name
