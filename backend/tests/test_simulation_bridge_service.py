"""service.py の実行前検査・契約の補完・実行時エラーの事実文（実機 2026-10-03 の KeyError: 'b_1' の是正）。"""

from __future__ import annotations

from core.simulation_bridge import ast_guard, codegen, schema, service

SRC = """
import numpy as np
def simulate(inputs, params):
    k = inputs["k1"]
    b = params["b_1"]
    beta = params.get("beta_2", 0.0)
    extra = params.get("gamma")
    return {"Z": k * b + beta}
"""


def test_required_keys_distinguishes_required_and_optional():
    keys = ast_guard.required_keys(SRC)
    assert keys["inputs"] == {"k1": True}
    assert keys["params"]["b_1"] is True
    assert keys["params"]["beta_2"] is False
    assert keys["params"]["gamma"] is True
    assert ast_guard.required_keys("def (") == {"inputs": {}, "params": {}}


def test_complete_contract_adds_keys_used_by_code():
    contract = {"inputs": [{"symbol": "k1", "kind": "variable", "unit": "", "default": 0.1}], "outputs": [], "parameters": []}
    out = codegen.complete_contract_from_source(contract, SRC)
    params = {i["symbol"]: i for i in out["parameters"]}
    assert set(params) == {"b_1", "beta_2", "gamma"}
    assert params["b_1"]["default"] is None and params["b_1"]["kind"] == "parameter"
    assert out["inputs"] == contract["inputs"]  # 既存は変えない


def test_missing_required_inputs_names_only_required_keys():
    missing = service.missing_required_inputs(SRC, values={"k1": 0.1}, params={"beta_2": 0.0})
    assert missing == ["b_1", "gamma"]
    assert service.missing_required_inputs(SRC, values={"k1": 0.1}, params={"b_1": 1.0, "gamma": 0.0}) == []


class _Sandbox:
    def __init__(self):
        self.calls = 0

    def run(self, *a, **k):
        self.calls += 1
        return schema.SandboxResult(status=schema.RUN_STATUS_FAILED, error="KeyError: 'b_1'\n  File x")


def test_run_pipeline_returns_named_fact_before_touching_sandbox():
    sb = _Sandbox()
    status, result, runner = service.run_pipeline(
        program={"source_code": SRC, "provenance": {}}, wiring=None, catalog={}, values={"k1": 0.1}, params={},
        instrument_params=None, reference=None, seed=None, sandbox_client=sb,
    )
    assert status == schema.RUN_STATUS_FAILED
    assert sb.calls == 0
    assert any("b_1" in f for f in result["facts"])
    assert all("KeyError" not in f for f in result["facts"])


def test_run_pipeline_surfaces_sanitized_runtime_error():
    sb = _Sandbox()
    status, result, runner = service.run_pipeline(
        program={"source_code": SRC, "provenance": {}}, wiring=None, catalog={}, values={"k1": 0.1},
        params={"b_1": 1.0, "gamma": 0.0}, instrument_params=None, reference=None, seed=None, sandbox_client=sb,
    )
    assert status == schema.RUN_STATUS_FAILED and sb.calls == 1
    assert result["facts"] == [service.FACT_RUNTIME_ERROR.format(head="KeyError: 'b_1'")]


def test_error_facts_skip_configuration_errors():
    assert service.error_facts("not_configured") == []
    assert service.error_facts("") == []
    assert service.error_facts("\x1b[31mValueError: x\x1b[0m")[0].endswith("ValueError: x")


# ---------------------------------------------------------------------------
# 第 21 周の是正（R1 / R5 / R7 / R16 / 未測定の共有）
# ---------------------------------------------------------------------------

GOOD = "def simulate(inputs, params):\n    return {'y': 2.0, 'z': [1.0, 3.0]}\n"


class _OkSandbox:
    def __init__(self, observed=None):
        self.observed = observed

    def run(self, source, *, entrypoint, inputs, params, seed):
        if entrypoint == schema.PROGRAM_ENTRYPOINT:
            return schema.SandboxResult(status=schema.RUN_STATUS_SUCCEEDED, outputs={"y": 2.0, "z": [1.0, 3.0]})
        return schema.SandboxResult(status=schema.RUN_STATUS_SUCCEEDED, outputs={"observed": self.observed or {}})


def _run(**kw):
    return service.run_pipeline(
        program={"source_code": GOOD, "provenance": {}}, catalog={}, values={}, params={},
        instrument_params=None, reference=None, seed=1, sandbox_client=_OkSandbox(), **kw,
    )


class TestRound21Pipeline:
    def test_labels_carry_axis_names(self):
        status, result, _ = _run(wiring=None, module_label="M")
        assert status == schema.RUN_STATUS_SUCCEEDED
        assert result["labels"] == {
            "agreement": "一致（理論の出力と模擬観測）: 未測定",
            "separation": "分離（効果とばらつき）: 未測定",
            "reference": "記載値との一致: 未測定",
        }

    def test_axis_labels_cover_every_level(self):
        from core import label_vocab

        for level in schema.AGREEMENT_LEVELS:
            labels = service.axis_labels(level, schema.SEPARATION_LEVEL_UNMEASURED, level)
            assert labels["agreement"].endswith(": " + label_vocab.SIMULATION_AGREEMENT_LABELS[level])
            assert labels["agreement"].startswith(label_vocab.SIMULATION_AXIS_LABELS["agreement"])

    def test_statement_and_facts_do_not_repeat_the_agreement_sentence(self):
        _, result, _ = _run(wiring=None, module_label="M")
        unmeasured = schema.AGREEMENT_FACTS[schema.AGREEMENT_LEVEL_UNMEASURED]
        assert unmeasured in result["statement"]
        assert not any(unmeasured in f for f in result["facts"])
        assert not any(unmeasured in f for f in result["comparison"]["facts"])
        # 一致以外の事実文（分離・記載値・配線なし・検証ではない）は残る
        assert schema.FACT_NO_WIRING in result["facts"]
        assert result["facts"][-1] == schema.FACT_NOT_VERIFICATION

    def test_without_statement_duplicate_keeps_facts_when_statement_empty(self):
        facts = ["a", "b"]
        assert service.without_statement_duplicate(facts, "", "a") == ["a", "b"]
        assert service.without_statement_duplicate(facts, "s", "a") == ["b"]
        assert service.without_statement_duplicate(["s", "b"], "s", "x") == ["b"]

    def test_no_wiring_observed_is_the_theory_output(self):
        _, result, _ = _run(wiring=None)
        assert result["observed"] == {"y": {"mean": 2.0, "std": None}, "z": {"mean": 2.0, "std": None}}
        # 比較の意味は変えない（配線なしは未測定）
        assert result["comparison"]["agreement_level"] == schema.AGREEMENT_LEVEL_UNMEASURED

    def test_observed_from_theory_skips_non_numeric(self):
        assert service.observed_from_theory({"a": "x", "b": 1}) == {"b": {"mean": 1.0, "std": None}}


class TestRound21Helpers:
    def test_is_unmeasured_run(self):
        u = schema.AGREEMENT_LEVEL_UNMEASURED
        assert service.is_unmeasured_run({"result": {"comparison": {
            "agreement_level": u, "separation_level": u, "reference_level": u}}})
        assert service.is_unmeasured_run({"result": {}})
        assert not service.is_unmeasured_run({"result": {"comparison": {
            "agreement_level": u, "separation_level": "clear", "reference_level": u}}})
        assert not service.is_unmeasured_run({"result": {"comparison": {
            "agreement_level": u, "separation_level": u, "reference_level": "match"}}})

    def test_readable_target_label(self):
        assert service.is_readable_target_label("k を測る")
        assert service.is_readable_target_label("We measure the spectral index.")  # 教材の言語は残す
        for bad in ("", "  ", "（本文を特定できない要素）", "claim:tex_b19:span_002", "span_003", "tex_b19 の主張"):
            assert not service.is_readable_target_label(bad), bad

    def test_readable_observation_targets_filters(self):
        rows = [
            {"claim_id": "c1", "label": "k を測る"},
            {"claim_id": "c2", "label": "（本文を特定できない要素）"},
            {"claim_id": "c3", "label": "claim:tex_b19:span_002"},
            {"claim_id": "", "label": "本文"},
            "junk",
        ]
        assert [t["claim_id"] for t in service.readable_observation_targets(rows)] == ["c1"]


class TestRound21ModuleItems:
    def _items(self, module):
        return service.module_items(
            [module], records={}, equations_by_id={}, latest_program_by_module={}, program_dto=lambda p: None,
        )[0]

    def test_member_equations_and_to_sink_are_exposed(self):
        item = self._items({
            "module_key": "m1",
            "member_equations": [
                {"equation_id": "eq_tex_1", "display_label": "式 (3)", "latex_snippet": "$a=b$",
                 "defined_symbols": ["a"]},
            ],
            "to_sink": [{"equation_id": "eq_tex_2", "display_label": "式 (4)"}],
        })
        assert item["equations"] == [{"display_label": "式 (3)", "latex_snippet": "$a=b$", "defined_symbols": ["a"]}]
        assert item["to_sink"] == [{"equation_id": "eq_tex_2", "display_label": "式 (4)"}]

    def test_missing_keys_give_empty_lists(self):
        item = self._items({"module_key": "m1"})
        assert item["equations"] == [] and item["to_sink"] == []
