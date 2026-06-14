"""Tests for repair._parse_record label / equation_id re-validation (issue #368)."""
from episteme_graph.agents.equation_semantics.repair import _parse_record
from episteme_graph.agents.equation_semantics.schema import EquationLLMInput
from episteme_graph.agents.equation_semantics.validator import EquationSemanticsValidator
from episteme_graph.agents.equation_semantics.schema import EquationSemanticsResult


def _llm_input(label="3.14", equation_id="eq_3_14", block_id="blk_e1", source_is_trusted=False):
    return EquationLLMInput(
        document_id="doc",
        cartridge_id=None,
        equation_id=equation_id,
        block_id=block_id,
        section_id="sec_1",
        section_title="Sec",
        backbone_block_type=None,
        label=label,
        equation_text="N = a + b",
        latex=None,
        plain_text="N = a + b",
        prev_texts=["ctx"],
        next_texts=["ctx"],
        nearby_span_annotations=[],
        extraction_status="complete",
        acceptance_status="accepted",
        needs_reconstruction=False,
        extraction_source="pdf_text_layer",
        source_is_trusted=source_is_trusted,
    )


def _base_raw(**over):
    raw = {
        "equation_type": "definition",
        "secondary_types": [],
        "semantic_status": "source_backed",
        "confidence": 0.9,
        "reason": "ok",
        "defined_symbols": [{"symbol": "N", "definition_status": "defined"}],
        "used_symbols": ["a", "b"],
        "assumptions": [],
        "input_equation_ids": [],
        "output_equation_ids": [],
        "linked_text_spans": [],
        "summary": "Definition of N.",
        "review_flags": [],
    }
    raw.update(over)
    return raw


def _validate(record):
    result = EquationSemanticsResult("doc", None, [], [record])
    return EquationSemanticsValidator().validate(result)


# ------------------------------------------------------------------
# invalid LLM label
# ------------------------------------------------------------------

def test_invalid_llm_label_does_not_enter_final_record():
    raw = _base_raw(label="(1) g , K (3)", equation_id="eq_3_14")
    record = _parse_record(raw, _llm_input(label="3.14", equation_id="eq_3_14"), None)
    # canonical label kept; garbage LLM label rejected
    assert record.label == "3.14"
    assert record.equation_id == "eq_3_14"
    assert record.source_extraction.source_location["llm_rejected_label"] == "(1) g , K (3)"
    issues = _validate(record)
    assert any(i.rule_id == "llm_label_overridden" for i in issues)


def test_invalid_llm_equation_id_keeps_normalized_id():
    raw = _base_raw(label="3.14", equation_id="eq_ (1) g , K")
    record = _parse_record(raw, _llm_input(label="3.14", equation_id="eq_3_14"), None)
    assert record.equation_id == "eq_3_14"
    assert record.source_extraction.source_location["llm_rejected_equation_id"] == "eq_ (1) g , K"
    issues = _validate(record)
    assert any(i.rule_id == "llm_equation_id_overridden" for i in issues)


def test_valid_llm_label_is_not_adopted_canonical_maintained():
    # Even a syntactically valid different label must not replace the canonical
    # input label / id (issue #368 follow-up): both stay pinned to llm_input.
    raw = _base_raw(label="3.15", equation_id="eq_3_15")
    record = _parse_record(raw, _llm_input(label="3.14", equation_id="eq_3_14"), None)
    assert record.label == "3.14"
    assert record.equation_id == "eq_3_14"
    assert record.source_extraction.source_location["llm_rejected_label"] == "3.15"
    assert record.source_extraction.source_location["llm_rejected_equation_id"] == "eq_3_15"
    issues = _validate(record)
    assert any(i.rule_id == "llm_label_overridden" for i in issues)
    assert any(i.rule_id == "llm_equation_id_overridden" for i in issues)


def test_llm_output_cannot_collide_ids_across_records():
    # Two records whose LLM outputs both claim "eq_x" must keep their distinct
    # canonical ids — no LLM-driven id collision (issue #368 D.7).
    raw_a = _base_raw(equation_id="eq_x", label="eq_x")
    raw_b = _base_raw(equation_id="eq_x", label="eq_x")
    rec_a = _parse_record(raw_a, _llm_input(label="3.14", equation_id="eq_3_14"), None)
    rec_b = _parse_record(raw_b, _llm_input(label="3.15", equation_id="eq_3_15"), None)
    assert rec_a.equation_id == "eq_3_14"
    assert rec_b.equation_id == "eq_3_15"
    assert rec_a.equation_id != rec_b.equation_id


def test_trusted_tex_semantic_label_is_maintained():
    raw = _base_raw(label="eq:dispersion", equation_id="eq_eq_dispersion")
    record = _parse_record(
        raw,
        _llm_input(label="eq:dispersion", equation_id="eq_eq_dispersion", source_is_trusted=True),
        None,
    )
    assert record.label == "eq:dispersion"
    assert record.equation_id == "eq_eq_dispersion"
    # nothing rejected
    assert "llm_rejected_label" not in record.source_extraction.source_location


def test_no_llm_label_keeps_canonical():
    raw = _base_raw()
    raw.pop("label", None)
    record = _parse_record(raw, _llm_input(label="2.1", equation_id="eq_2_1"), None)
    assert record.label == "2.1"
    assert record.equation_id == "eq_2_1"
    assert "llm_rejected_label" not in record.source_extraction.source_location
