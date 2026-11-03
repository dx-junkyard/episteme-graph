"""Tests for ThesisReconstructionValidator."""
from episteme_graph.agents.thesis_reconstruction.schema import CartridgeContext, ThesisReconstructionResult, THESIS_VERSION
from episteme_graph.agents.thesis_reconstruction.validator import ThesisReconstructionValidator

VALIDATOR = ThesisReconstructionValidator()


def _result(**kwargs):
    defaults = dict(
        document_id="doc",
        thesis_version=THESIS_VERSION,
        cartridge_id=None,
        central_thesis={
            "text": "A sum rule relates R Lambda c, RD, and RD*.",
            "claim_ids": ["claim:b2:s1"],
            "equation_ids": ["eq_3_14"],
            "evidence_block_ids": ["b2"],
            "reason": "core relation",
            "confidence": 0.9,
        },
        alternative_theses=[],
        support_structure={
            "direct_supports": [
                {
                    "support_id": "sup_1",
                    "text": "The derivation supplies the relation.",
                    "claim_ids": ["claim:b2:s1"],
                    "equation_ids": ["eq_3_14"],
                    "support_type": "derivation_support",
                    "reason": "equation support",
                    "confidence": 0.85,
                }
            ],
            "assumptions": [],
            "derivation_core": [],
            "correction_sources": [],
            "uncertainty_sources": [],
            "diagnostic_consequences": [],
            "future_requirements": [],
        },
        excluded_from_core=[{"category": "prior_work", "claim_ids": ["claim:b0:s2"], "reason": "background"}],
        thesis_graph_hints=[{"from": "central_thesis", "to": "support:direct_supports:0", "relation": "supported_by"}],
        review_notes=[],
        confidence=0.88,
    )
    defaults.update(kwargs)
    return ThesisReconstructionResult(**defaults)


def test_valid_result_has_no_errors():
    assert not [i for i in VALIDATOR.validate(_result()) if i.severity == "error"]


def test_empty_central_thesis_is_error():
    r = _result(central_thesis={**_result().central_thesis, "text": ""})
    assert any(i.rule_id == "empty_central_thesis" for i in VALIDATOR.validate(r))


def test_missing_direct_supports_is_error():
    support = {**_result().support_structure, "direct_supports": []}
    assert any(i.rule_id == "missing_direct_supports" for i in VALIDATOR.validate(_result(support_structure=support)))


def test_invalid_support_type_is_error():
    support = _result().support_structure
    support["direct_supports"] = [{**support["direct_supports"][0], "support_type": "bad"}]
    assert any(i.rule_id == "invalid_support_type" for i in VALIDATOR.validate(_result(support_structure=support)))


def test_invalid_graph_relation_is_error():
    r = _result(thesis_graph_hints=[{"from": "x", "to": "y", "relation": "bad"}])
    assert any(i.rule_id == "invalid_graph_relation" for i in VALIDATOR.validate(r))


def test_confidence_out_of_range_is_error():
    assert any(i.rule_id == "confidence_out_of_range" for i in VALIDATOR.validate(_result(confidence=2.0)))


def test_meta_central_thesis_is_warning():
    r = _result(central_thesis={**_result().central_thesis, "text": "Figure 1 shows the result."})
    assert any(i.rule_id == "central_thesis_is_meta" for i in VALIDATOR.validate(r))


def test_cartridge_terms_warning_when_absent():
    cartridge = CartridgeContext("test", {}, {}, aliases={"R Lambda c": ["RΛc"]})
    r = _result(central_thesis={**_result().central_thesis, "text": "A relation is established."})
    assert any(i.rule_id == "cartridge_terms_not_reflected" for i in VALIDATOR.validate(r, cartridge))


def test_japanese_generated_prose_is_accepted():
    """生成言語 ja（IK-0571）: 日本語の中心命題・支持の文は error にならない。"""
    result = _result()
    result.central_thesis = dict(result.central_thesis, text="和則が R Lambda c と RD、RD* を結びつける。", reason="中心の関係式")
    result.support_structure["direct_supports"][0]["text"] = "導出がこの関係を与える。"
    result.alternative_theses = [{"text": "別の定式化として、比の関係を主張している。", "reason": "代替", "confidence": 0.4}]
    assert not [i for i in VALIDATOR.validate(result) if i.severity == "error"]
