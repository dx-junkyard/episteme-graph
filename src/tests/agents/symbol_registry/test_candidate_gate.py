"""記号候補の形式ゲートと単位抽出（2026-09-19）。

2026-09-15 のコーパスでは ``0.015`` ``1`` ``10^-2`` のような数値リテラル、
``angular variables / basis functions`` のような語句、``\\begin{pmatrix}…`` のような
環境、``Equation12`` ``Eq. (3.7)`` のような参照語が canonical_symbol として登録
されていた。書式だけで弾き（分野語をハードコードしない）、弾いた候補は理由付きで
coverage に残す（P4）。
"""
from __future__ import annotations

from episteme_graph.agents.coverage_report import is_coverage_report
from episteme_graph.agents.equation_semantics.schema import DefinedSymbol
from episteme_graph.agents.symbol_registry.builder import (
    SymbolRegistryBuilder,
    exclusion_reason,
    extract_unit,
    normalize_symbol,
)
from episteme_graph.agents.symbol_registry.schema import EXCLUSION_REASONS

from .test_builder import _equation, _result

BUILDER = SymbolRegistryBuilder()


def _reason(raw: str) -> str | None:
    return exclusion_reason(raw, normalize_symbol(raw))


def test_numeric_literals_are_not_symbols():
    for raw in ("0.015", "0.25", "1", "4", "10^-2", "2!", "(3,2)"):
        assert _reason(raw) in {"numeric_literal", "expression"}, raw


def test_phrases_are_not_symbols():
    for raw in (
        "2-dimensional covariant derivatives on the sphere",
        "angular variables / basis functions",
        "radial derivative",
    ):
        assert _reason(raw) == "phrase", raw


def test_latex_environments_and_references_are_not_symbols():
    assert _reason("\\begin{pmatrix} a \\end{pmatrix}") == "latex_environment"
    for raw in ("Equation12", "Eq. (3.7)", "Fig. 2", "Table III", "Sec. 4"):
        assert _reason(raw) == "document_reference", raw


def test_parenthesised_expressions_are_not_symbols():
    assert _reason("(2π)^3") == "expression"


def test_real_symbols_pass_the_gate():
    for raw in (
        "z", "k", "T", "SNR", "b_1", "H_0", "sigma_8", "\\rho", "\\beta",
        "P_L(k)", "f_{\\mathrm{Nyq}}", "w(\\theta)", "\\Delta \\chi^2",
        "d^2N/dzd\\Omega",
    ):
        assert _reason(raw) is None, raw


def test_every_reason_is_declared_in_the_schema_vocabulary():
    for raw in (
        "0.015", "(2π)^3", "radial derivative",
        "\\begin{pmatrix}a\\end{pmatrix}", "Equation12", "x" * 60,
    ):
        reason = _reason(raw)
        assert reason in EXCLUSION_REASONS, (raw, reason)


def test_excluded_candidates_are_kept_in_coverage_with_their_reason():
    eq = _equation(
        "eq_1",
        defined=[DefinedSymbol("\\beta", "defined", "β is the bias")],
        used=["0.015", "radial derivative", "z"],
    )
    result = BUILDER.run(_result([eq]))

    canonical = {r.canonical_symbol for r in result.records}
    assert canonical == {"β", "z"}

    coverage = result.coverage
    assert is_coverage_report(coverage)
    assert coverage["population"] == 4
    assert coverage["processed"] == 2
    assert coverage["truncated"] == 2
    assert set(coverage["reasons"]) == {"numeric_literal", "phrase"}
    excluded = {e["raw"]: e for e in coverage["details"]["excluded"]}
    assert set(excluded) == {"0.015", "radial derivative"}
    assert excluded["0.015"]["reason"] == "numeric_literal"
    assert excluded["0.015"]["equation_ids"] == ["eq_1"]


def test_coverage_is_present_even_when_nothing_is_excluded():
    result = BUILDER.run(_result([_equation("eq_1", used=["z"])]))
    assert is_coverage_report(result.coverage)
    assert result.coverage["truncated"] == 0
    assert result.coverage["reasons"] == []
    assert "details" not in result.coverage


def test_coverage_round_trips_through_to_dict():
    result = BUILDER.run(_result([_equation("eq_1", used=["0.25", "z"])]))
    from episteme_graph.agents.symbol_registry.schema import SymbolRegistryResult

    restored = SymbolRegistryResult.from_dict(result.to_dict())
    assert restored.coverage == result.coverage


# ---------------------------------------------------------------------------
# unit
# ---------------------------------------------------------------------------


def test_unit_is_extracted_only_from_an_explicit_statement():
    assert extract_unit(["the distance is given in units of Mpc"]) == "Mpc"
    assert extract_unit(["measured in units of km/s."]) == "km/s"
    assert extract_unit(["the mass [M_sun] of the halo", "unit: [GeV]"]) == "GeV"
    # 全小文字の語句は単位記号の書式ではないので採らない（保守側）。
    assert extract_unit(["expressed in units of the critical density"]) is None
    assert extract_unit(["no unit is stated here"]) is None
    assert extract_unit([]) is None
    assert extract_unit(None) is None


def test_symbol_record_carries_the_extracted_unit():
    eq = _equation(
        "eq_1",
        defined=[DefinedSymbol("H_0", "defined", "H_0 is the Hubble constant in units of km/s")],
    )
    record = BUILDER.run(_result([eq])).records[0]
    assert record.unit == "km/s"


def test_symbol_record_unit_stays_none_when_the_source_is_silent():
    eq = _equation("eq_1", defined=[DefinedSymbol("z", "defined", "z is the redshift")])
    assert BUILDER.run(_result([eq])).records[0].unit is None
