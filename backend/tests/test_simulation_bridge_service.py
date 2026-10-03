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
