"""Tests for SymbolRegistryBuilder (issue #355)."""
import json

from episteme_graph.agents.equation_semantics.schema import (
    DefinedSymbol,
    EquationConfidencePolicy,
    EquationReconstruction,
    EquationRecord,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
)
from episteme_graph.agents.symbol_registry.builder import (
    SymbolRegistryBuilder,
    normalize_symbol,
)
from episteme_graph.agents.symbol_registry.schema import SymbolRegistryResult
from episteme_graph.agents.symbol_registry.validator import SymbolRegistryValidator


def _equation(
    equation_id,
    *,
    section_id="sec_1",
    defined=None,
    used=None,
    evidence_ids=None,
):
    src = EquationSourceExtraction(
        raw_text="x = y",
        latex=None,
        plain_text="x = y",
        source_location={"page": 1, "section_id": section_id,
                         "block_id": f"b_{equation_id}", "bbox": None},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
    )
    rec = EquationReconstruction.make_none()
    sem = EquationSemantics(
        equation_type="relation",
        secondary_types=[],
        semantic_status="source_backed",
        confidence=0.9,
        reason="r",
        defined_symbols=list(defined or []),
        used_symbols=list(used or []),
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=list(evidence_ids or []),
        linked_claim_ids=[],
        summary="",
        review_flags=[],
    )
    policy = EquationConfidencePolicy.derive(src, rec, sem)
    return EquationRecord(
        equation_id=equation_id,
        document_id="doc",
        label=equation_id,
        candidate_trace_ids=[],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=policy,
    )


def _result(equations):
    return EquationSemanticsResult("doc", None, [], list(equations))


BUILDER = SymbolRegistryBuilder()


def test_normalize_symbol_merges_latex_and_unicode():
    assert normalize_symbol("\\beta") == "β"
    assert normalize_symbol("β") == "β"
    assert normalize_symbol("\\mathrm{b}_1") == "b_1"
    assert normalize_symbol("$x$") == "x"
    assert normalize_symbol("B") == "B"  # case preserved


def test_variants_aggregate_into_one_record():
    eq1 = _equation("eq_1", defined=[DefinedSymbol("\\beta", "defined", "β is the bias")],
                    evidence_ids=["ev_1"])
    eq2 = _equation("eq_2", used=["β"])
    result = BUILDER.run(_result([eq1, eq2]))
    assert len(result.records) == 1
    record = result.records[0]
    assert record.canonical_symbol == "β"
    assert set(record.notation_variants) == {"\\beta", "β"}
    assert record.defining_equation_ids == ["eq_1"]
    assert record.used_in_equation_ids == ["eq_2"]
    assert record.source_evidence_ids == ["ev_1"]
    assert record.definition_status == "defined"
    assert record.definition_evidence_texts == ["β is the bias"]


def test_redefinition_is_detected_with_review_reason():
    eq1 = _equation("eq_1", defined=[DefinedSymbol("b_1", "defined", "first def")])
    eq2 = _equation("eq_2", defined=[DefinedSymbol("b_1", "defined", "second def")])
    result = BUILDER.run(_result([eq1, eq2]))
    record = result.records[0]
    assert record.definition_status == "redefined"
    assert "symbol_redefinition" in record.review_reasons


def test_used_only_symbol_keeps_definition_missing_reason():
    eq1 = _equation("eq_1", used=["σ_8"])
    result = BUILDER.run(_result([eq1]))
    record = result.records[0]
    assert record.definition_status == "used"
    assert "definition_missing" in record.review_reasons
    assert record.unit is None
    assert record.domain_constraints == []


def test_scope_derivation():
    eq1 = _equation("eq_1", section_id="sec_1",
                    defined=[DefinedSymbol("a", "defined", None)])
    eq2 = _equation("eq_2", section_id="sec_1", used=["a", "b"])
    eq3 = _equation("eq_3", section_id="sec_2", used=["a"])
    eq4 = _equation("eq_4", section_id="sec_1", used=["c"])
    result = BUILDER.run(_result([eq1, eq2, eq3, eq4]))
    by_symbol = {r.canonical_symbol: r for r in result.records}
    assert by_symbol["a"].scope == "document"      # 3 equations, 2 sections
    assert by_symbol["b"].scope == "equation_local"
    assert by_symbol["c"].scope == "equation_local"


def test_annotates_defined_symbols_with_symbol_id():
    ds = DefinedSymbol("\\beta", "defined", "β is the bias")
    eq1 = _equation("eq_1", defined=[ds])
    result = BUILDER.run(_result([eq1]))
    assert ds.symbol_id == result.records[0].symbol_id


def test_works_without_cartridge_and_kind_is_unknown():
    eq1 = _equation("eq_1", defined=[DefinedSymbol("R_D", "defined", None)])
    result = BUILDER.run(_result([eq1]), cartridge_id=None)
    assert result.records[0].kind == "unknown"


def test_missing_cartridge_id_degrades_gracefully():
    eq1 = _equation("eq_1", defined=[DefinedSymbol("x", "defined", None)])
    result = BUILDER.run(_result([eq1]), cartridge_id="no_such_cartridge")
    assert len(result.records) == 1


def test_cartridge_notation_pattern_classifies_kind(tmp_path):
    cartridge_dir = tmp_path / "test_cartridge"
    cartridge_dir.mkdir()
    (cartridge_dir / "ontology.json").write_text(json.dumps({
        "aliases": [{"canonical": "R Lambda c", "aliases": ["RΛc", "R_Λc"]}],
        "notation_patterns": [
            {"id": "wilson", "pattern": "C_[A-Za-z0-9]+", "concept_type": "Parameter"},
        ],
    }), encoding="utf-8")
    builder = SymbolRegistryBuilder(cartridge_base_dir=str(tmp_path))
    eq1 = _equation("eq_1", defined=[DefinedSymbol("C_9", "defined", None)],
                    used=["RΛc"])
    eq2 = _equation("eq_2", used=["R_Λc"])
    result = builder.run(_result([eq1, eq2]), cartridge_id="test_cartridge")
    by_symbol = {r.canonical_symbol: r for r in result.records}
    assert by_symbol["C_9"].kind == "parameter"
    # cartridge aliases merge variants under the canonical name
    assert "R Lambda c" in by_symbol
    assert set(by_symbol["R Lambda c"].notation_variants) == {"RΛc", "R_Λc"}


def test_result_is_json_serializable_and_round_trips():
    eq1 = _equation("eq_1", defined=[DefinedSymbol("β", "defined", "def")],
                    used=["x"])
    result = BUILDER.run(_result([eq1]))
    payload = result.to_json()
    reloaded = SymbolRegistryResult.from_dict(json.loads(payload))
    assert reloaded.document_id == "doc"
    assert len(reloaded.records) == len(result.records)
    assert reloaded.records[0].symbol_id == result.records[0].symbol_id


def test_builder_is_deterministic():
    def build():
        eq1 = _equation("eq_1", defined=[DefinedSymbol("\\beta", "defined", "d")],
                        used=["x", "y"])
        eq2 = _equation("eq_2", used=["β", "x"])
        return BUILDER.run(_result([eq1, eq2])).to_dict()

    assert build() == build()


def test_validator_passes_builder_output():
    eq1 = _equation("eq_1", defined=[DefinedSymbol("b_1", "defined", "d")],
                    used=["σ_8"])
    eq2 = _equation("eq_2", defined=[DefinedSymbol("b_1", "defined", "d2")])
    result = BUILDER.run(_result([eq1, eq2]))
    issues = SymbolRegistryValidator().validate(result)
    assert [i for i in issues if i.severity == "error"] == []


def test_validator_detects_invalid_vocab():
    eq1 = _equation("eq_1", defined=[DefinedSymbol("a", "defined", None)])
    result = BUILDER.run(_result([eq1]))
    result.records[0].kind = "banana"
    result.records[0].scope = "galaxy"
    issues = SymbolRegistryValidator().validate(result)
    rule_ids = [i.rule_id for i in issues]
    assert "invalid_symbol_kind" in rule_ids
    assert "invalid_symbol_scope" in rule_ids


def test_empty_equations_result_is_empty_registry():
    result = BUILDER.run(_result([]))
    assert result.records == []
    assert result.document_id == "doc"
