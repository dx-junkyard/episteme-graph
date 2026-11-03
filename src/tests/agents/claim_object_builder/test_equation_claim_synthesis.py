"""Tests for issue #388 — atomic claim synthesis from equations / derivations."""

from __future__ import annotations

from episteme_graph.agents.claim_object_builder.equation_claim_synthesis import (
    _math,
    synthesize_equation_claims,
)
from episteme_graph.agents.derivation_chain.schema import (
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)
from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    EquationConfidencePolicy,
    EquationReconstruction,
    EquationRecord,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
)


def _make_eq(eq_id, *, role, defined=None, used=None, latex="x=y", evidence=None, label=None):
    src = EquationSourceExtraction(
        raw_text="x",
        latex=latex,
        plain_text="x=y",
        source_location={"page": 1, "section_id": "doc:sec", "block_id": f"blk_{eq_id}", "bbox": []},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
        needs_math_review=False,
        review_reason=[],
    )
    rec = EquationReconstruction.make_none()
    sem = EquationSemantics(
        equation_type=role,
        secondary_types=[],
        semantic_status="source_backed",
        confidence=0.85,
        reason="",
        defined_symbols=[DefinedSymbol(symbol=s, definition_status="defined") for s in (defined or [])],
        used_symbols=list(used or []),
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=list(evidence or []),
        linked_claim_ids=[],
        summary="",
        review_flags=[],
    )
    cp = EquationConfidencePolicy.derive(src, rec, sem)
    return EquationRecord(
        equation_id=eq_id, document_id="doc", label=label if label is not None else eq_id,
        candidate_trace_ids=[f"eqcand_{eq_id}"],
        source_extraction=src, reconstruction=rec, semantics=sem, confidence_policy=cp,
    )


def _eqs(equations):
    return EquationSemanticsResult(
        document_id="doc", cartridge_id=None, equation_candidates=[],
        equations=equations, validation_issues=[],
    )


def test_definition_equation_yields_equation_backed_definition_claim():
    eqs = _eqs([_make_eq("eq_1", role="definition", defined=["S3"], evidence=["ev_1"])])
    claims = synthesize_equation_claims(eqs)
    assert len(claims) == 1
    c = claims[0]
    assert c.claim_type == "definition_claim"
    assert c.support_status == "equation_backed"
    assert c.equation_ids == ["eq_1"]
    assert c.source_evidence_ids == ["ev_1"]
    assert c.is_atomic is True
    assert c.text == "An equation of this paper defines $S3$."
    assert c.normalized_text == c.text


def test_relation_equation_yields_dependency_claim():
    eqs = _eqs([_make_eq("eq_2", role="relation", defined=["y"], used=["a", "b"], evidence=["ev_2"])])
    claims = synthesize_equation_claims(eqs)
    assert any(c.claim_type == "dependency_claim" for c in claims)
    c = next(c for c in claims if c.claim_type == "dependency_claim")
    assert c.text == "In an equation of this paper, $y$ depends on $a$, $b$."


def test_unsourced_equation_marks_review_required():
    eqs = _eqs([_make_eq("eq_3", role="definition", defined=["z"], latex=None, evidence=[])])
    claims = synthesize_equation_claims(eqs)
    assert claims[0].support_status == "review_required"
    assert "equation_not_source_backed" in claims[0].review_reasons


def test_existing_prose_claim_equation_is_not_duplicated():
    eqs = _eqs([_make_eq("eq_1", role="definition", defined=["S3"], evidence=["ev_1"])])

    class _Prose:
        equation_ids = ["eq_1"]

    claims = synthesize_equation_claims(eqs, existing_claims=[_Prose()])
    assert claims == []


def test_system_derivation_yields_equation_system_claim():
    eqs = _eqs([_make_eq("eq_1", role="relation", used=["a", "b"])])
    chain = DerivationChainRecord(
        derivation_id="system_derivation_0001",
        document_id="doc",
        source_section_ids=[],
        steps=[DerivationStep(step_id="s1", input_equation_ids=["eq_1"], operation="eliminate", output_equation_ids=["eq_2"])],
        chain_type="system_level",
        operation="eliminate",
        input_equation_ids=["eq_1"],
        output_equation_ids=["eq_2"],
        eliminated_symbols=["b"],
        retained_symbols=["a"],
        source_evidence_ids=["ev_1"],
    )
    derivations = DerivationChainResult(document_id="doc", cartridge_id=None, chains=[chain])
    claims = synthesize_equation_claims(eqs, derivations=derivations)
    system_claims = [c for c in claims if c.claim_type == "equation_system_claim"]
    assert system_claims
    sc = system_claims[0]
    assert sc.support_status == "derived_from_linked_artifacts"
    assert sc.derivation_ids == ["system_derivation_0001"]
    assert "b" in [cc.name for cc in sc.concepts]
    assert sc.text == "The equation system eliminates to eliminate $b$ while retaining $a$."


# ---------------------------------------------------------------------------
# Inline math delimiting (2026-09-03): raw LaTeX symbols interpolated into the
# synthesised prose are unrenderable, so every symbol is wrapped as ``$...$``.
# ---------------------------------------------------------------------------


def test_latex_symbols_are_dollar_delimited_in_claim_text():
    eqs = _eqs([
        _make_eq(
            "eq_tex", role="relation",
            defined=[r"F_n(\mathbf{k}_1)"],
            used=[r"\sum_{n=1}^{\infty}", r"\mathbf{k}"],
            evidence=["ev_1"],
        )
    ])
    claims = synthesize_equation_claims(eqs)
    assert claims[0].text == (
        r"In an equation of this paper, $F_n(\mathbf{k}_1)$ depends on "
        r"$\sum_{n=1}^{\infty}$, $\mathbf{k}$."
    )


def test_already_delimited_symbol_is_not_double_wrapped():
    eqs = _eqs([_make_eq("eq_d", role="definition", defined=["$P_L(k)$"], evidence=["ev_1"])])
    claims = synthesize_equation_claims(eqs)
    assert claims[0].text == "An equation of this paper defines $P_L(k)$."
    assert "$$" not in claims[0].text


def test_concept_names_carry_no_math_delimiters():
    eqs = _eqs([
        _make_eq("eq_c", role="relation", defined=[r"\rho"], used=["a", "b"], evidence=["ev_1"])
    ])
    claims = synthesize_equation_claims(eqs)
    names = [cc.name for cc in claims[0].concepts]
    assert names == [r"\rho", "a", "b"]
    assert all("$" not in n for n in names)


def test_system_derivation_without_eliminated_symbols_keeps_prose_fallback():
    eqs = _eqs([_make_eq("eq_1", role="relation", used=["a", "b"])])
    chain = DerivationChainRecord(
        derivation_id="system_derivation_0002",
        document_id="doc",
        source_section_ids=[],
        steps=[DerivationStep(step_id="s1", input_equation_ids=["eq_1"], operation="solve", output_equation_ids=[])],
        chain_type="system_level",
        operation="solve",
        input_equation_ids=["eq_1"],
        output_equation_ids=[],
        eliminated_symbols=[],
        retained_symbols=[],
        source_evidence_ids=["ev_1"],
    )
    derivations = DerivationChainResult(document_id="doc", cartridge_id=None, chains=[chain])
    claims = synthesize_equation_claims(eqs, derivations=derivations)
    sc = [c for c in claims if c.derivation_ids == ["system_derivation_0002"]][0]
    # フォールバック文言は記号ではないので数式化しない。
    assert sc.text == "The equation system supports a solve relating the listed quantities."


def test_math_helper_is_idempotent_and_empty_safe():
    assert _math("x") == "$x$"
    assert _math("  x  ") == "$x$"
    assert _math("$x$") == "$x$"
    assert _math(r"\(x\)") == r"\(x\)"
    assert _math("") == ""
    assert _math(None) == ""


# ---------------------------------------------------------------------------
# 2026-09-19: 合成文から内部 ID を締め出し、自己依存と非記号の $ 囲みをやめる。
# ---------------------------------------------------------------------------


def test_printed_equation_number_is_used_when_the_equation_is_numbered():
    eqs = _eqs([_make_eq("eq_blk_f79a66c8", role="definition", defined=["S3"],
                         evidence=["ev_1"], label="12")])
    claims = synthesize_equation_claims(eqs)
    assert claims[0].text == "Equation (12) defines $S3$."


def test_internal_identifier_never_reaches_the_claim_text():
    """``eq_*`` / ``eqcand_*`` / ``blk_*`` は読者に見えない内部 ID。"""
    eqs = _eqs([
        _make_eq("eq_eqcand_inline_blk_4aac98cb_1678_9f0ed0ad", role="relation",
                 defined=["y"], used=["a", "b"], evidence=["ev_1"], label=None),
        _make_eq("eq_blk_f79a66c8", role="definition", defined=["z"],
                 evidence=["ev_2"], label="blk_f79a66c8"),
    ])
    claims = synthesize_equation_claims(eqs)
    assert claims
    for claim in claims:
        for marker in ("eq_", "eqcand", "blk_"):
            assert marker not in claim.text, claim.text
            assert marker not in claim.normalized_text, claim.normalized_text


def test_self_dependency_is_not_written_as_a_claim():
    """``X depends on X`` は命題ではない。他の記号が無ければ合成しない。"""
    eqs = _eqs([
        _make_eq("eq_self", role="relation", defined=[], used=["f_Nyq", "f_Nyq"],
                 evidence=["ev_1"], label="4"),
    ])
    assert synthesize_equation_claims(eqs) == []


def test_target_symbol_is_dropped_from_its_own_dependency_list():
    eqs = _eqs([
        _make_eq("eq_dep", role="relation", defined=["y"], used=["y", "a", "y", "b"],
                 evidence=["ev_1"], label="7"),
    ])
    claims = synthesize_equation_claims(eqs)
    assert claims[0].text == "In equation (7), $y$ depends on $a$, $b$."


def test_non_symbol_names_are_written_as_plain_prose():
    """``Eq. (3.7)`` / ``radial derivative`` を ``$...$`` で囲うと壊れた数式になる。"""
    eqs = _eqs([
        _make_eq("eq_words", role="relation", defined=[r"f^{(2)}_{V}"],
                 used=["Eq. (3.7)", "radial derivative", r"\rho"],
                 evidence=["ev_1"], label="3.8"),
    ])
    claims = synthesize_equation_claims(eqs)
    assert claims[0].text == (
        r"In equation (3.8), $f^{(2)}_{V}$ depends on Eq. (3.7), "
        r"radial derivative, $\rho$."
    )


def test_is_printed_equation_label_accepts_only_reader_visible_numbers():
    from episteme_graph.agents.claim_object_builder.equation_claim_synthesis import (
        is_printed_equation_label,
    )

    for label in ("12", "3.7", "A.2", "S3", "(15)", "2.3.4", "3a"):
        assert is_printed_equation_label(label) is True, label
    for label in (
        None, "", "eq_1", "eq_blk_f79a66c8", "eqcand_inline_blk_4aac98cb",
        "blk_f79a66c8", "the first equation", "1234567890123",
    ):
        assert is_printed_equation_label(label) is False, label
