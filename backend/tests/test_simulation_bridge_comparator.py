"""SB層 比較器（core/simulation_bridge/comparator.py）と label_vocab の段階化。"""

from __future__ import annotations

import pytest

from core import label_vocab
from core.simulation_bridge import comparator, schema


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


class TestLevels:
    @pytest.mark.parametrize(
        "rel,level",
        [(0.0, "match"), (0.05, "match"), (0.0501, "rough"), (0.20, "rough"), (0.2001, "diverge"), (5.0, "diverge"),
         (None, "unmeasured"), ("x", "unmeasured"), (float("nan"), "unmeasured"), (-1.0, "unmeasured")],
    )
    def test_agreement(self, rel, level):
        assert label_vocab.simulation_agreement_level(rel) == level

    @pytest.mark.parametrize(
        "ratio,level",
        [(10.0, "clear"), (3.0, "clear"), (2.99, "border"), (1.0, "border"), (0.99, "buried"), (0.0, "buried"),
         (None, "unmeasured"), (float("inf"), "unmeasured")],
    )
    def test_separation(self, ratio, level):
        assert label_vocab.simulation_separation_level(ratio) == level

    def test_labels_cover_schema_levels(self):
        assert set(label_vocab.SIMULATION_AGREEMENT_LABELS) == set(schema.AGREEMENT_LEVELS)
        assert set(label_vocab.SIMULATION_SEPARATION_LABELS) == set(schema.SEPARATION_LEVELS)
        assert label_vocab.SIMULATION_AGREEMENT_THRESHOLDS == (0.05, 0.20)
        assert label_vocab.SIMULATION_SEPARATION_THRESHOLDS == (3.0, 1.0)


class TestCompare:
    def test_match_and_clear(self):
        c = comparator.compare({"y": 10.0}, {"y": {"mean": 10.4, "std": 1.0}}, wiring_name="増幅器A")
        assert c.agreement_level == "match"
        assert c.separation_level == "clear"
        assert c.metrics["relative_difference"] == pytest.approx(0.04)
        assert c.metrics["separation_ratio"] == pytest.approx(10.0)
        assert c.facts[0] == "このモデルと配線『増幅器A』のもとでは、" + schema.AGREEMENT_FACTS["match"]
        assert c.facts[-1] == schema.FACT_NOT_VERIFICATION
        assert schema.FACT_REFERENCE_MISSING in c.facts

    def test_worst_symbol_wins(self):
        c = comparator.compare(
            {"a": 1.0, "b": 1.0},
            {"a": {"mean": 1.0, "std": 0.1}, "b": {"mean": 1.5, "std": 0.6}},
        )
        assert c.agreement_level == "diverge"
        assert c.separation_level == "border"
        assert set(c.metrics["by_symbol"]) == {"a", "b"}

    def test_rough_and_buried(self):
        c = comparator.compare({"y": 1.0}, {"y": {"mean": 1.1, "std": 2.0}})
        assert (c.agreement_level, c.separation_level) == ("rough", "buried")

    def test_no_wiring(self):
        c = comparator.compare({"y": 2.0}, None)
        # 配線なしでは模擬観測＝理論出力なので「一致」を出さない（空疎な一致の禁止）
        assert c.agreement_level == "unmeasured"
        assert c.metrics["relative_difference"] is None
        assert c.separation_level == "unmeasured"
        assert schema.FACT_NO_WIRING in c.facts
        assert c.facts[-1] == schema.FACT_NOT_VERIFICATION
        assert c.facts[0].startswith(schema.FACT_CLOSED_WORLD_PREFIX)

    def test_std_zero_or_missing_is_unmeasured(self):
        assert comparator.compare({"y": 1.0}, {"y": {"mean": 1.0, "std": 0.0}}).separation_level == "unmeasured"
        assert comparator.compare({"y": 1.0}, {"y": {"mean": 1.0}}).separation_level == "unmeasured"

    def test_no_common_symbol(self):
        c = comparator.compare({"y": 1.0}, {"z": {"mean": 1.0, "std": 1.0}})
        assert c.agreement_level == "unmeasured"
        assert c.metrics["relative_difference"] is None

    def test_theory_zero_uses_epsilon(self):
        c = comparator.compare({"y": 0.0}, {"y": {"mean": 0.0, "std": 1.0}})
        assert c.agreement_level == "match"

    def test_list_theory_output(self):
        c = comparator.compare({"y": [1.0, 3.0]}, {"y": {"mean": 2.0, "std": 0.1}})
        assert c.agreement_level == "match"

    def test_reference(self):
        c = comparator.compare(
            {"y": 1.0},
            {"y": {"mean": 1.0, "std": 0.1}},
            reference={"y": {"value": 1.5, "unit": "m"}},
        )
        assert c.reference_level == "diverge"
        assert c.metrics["reference_relative_difference"] == pytest.approx(1 / 3)
        assert schema.FACT_REFERENCE_MISSING not in c.facts
        assert any("論文の記載値と乖離" in f for f in c.facts)

    def test_reference_uses_theory_without_wiring(self):
        c = comparator.compare({"y": 1.0}, None, reference={"y": {"value": 1.01}})
        assert c.reference_level == "match"

    def test_reference_missing(self):
        c = comparator.compare({"y": 1.0}, {"y": {"mean": 1.0, "std": 0.1}})
        assert c.reference_level == "unmeasured"
        assert c.metrics["reference_relative_difference"] is None

    def test_no_confidence_and_facts_have_no_digits(self):
        c = comparator.compare({"y": 3.0}, {"y": {"mean": 3.3, "std": 0.2}}, reference={"y": {"value": 3.1}})
        d = c.to_dict()
        assert not set(_walk_keys(d)) & schema.FORBIDDEN_DTO_KEYS
        for fact in d["facts"]:
            assert not any(ch.isdigit() for ch in fact), fact

    def test_statement(self):
        c = comparator.compare({"y": 1.0}, {"y": {"mean": 1.0, "std": 0.1}})
        s = comparator.comparison_statement(c, module_label="線形化", wiring_name="W")
        assert s == "このモデルと配線『W』のもとでは、理論モジュール『線形化』について、" + schema.AGREEMENT_FACTS["match"]
        s2 = comparator.comparison_statement(c.to_dict(), module_label="", wiring_name="")
        assert s2.startswith(schema.FACT_CLOSED_WORLD_PREFIX)
        assert comparator.comparison_statement(None, module_label="M", wiring_name="").endswith(
            schema.AGREEMENT_FACTS["unmeasured"]
        )
