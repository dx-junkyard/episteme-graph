"""SB層 実験機シミュレータの配線検査と合成（core/simulation_bridge/instrument.py）。"""

from __future__ import annotations

import pytest

from core.simulation_bridge import instrument, schema


def _amp(gain=2.0, gmin=0.0, gmax=10.0):
    return {
        "entry_id": "amp",
        "version_no": 1,
        "entry_type": schema.PART_TYPE_INSTRUMENT,
        "name": "線形増幅器",
        "body": {
            "transfer": "x * params['gain']",
            "params": {"gain": {"default": gain, "min": gmin, "max": gmax, "unit": ""}},
            "inputs": ["x"],
            "outputs": ["y"],
        },
    }


def _noise(kind="gaussian", param="sigma", default=0.5):
    return {
        "entry_id": f"noise_{kind}",
        "version_no": 1,
        "entry_type": schema.PART_TYPE_NOISE,
        "name": "ノイズ",
        "body": {"noise_kind": kind, "params": {param: {"default": default, "min": 0.0, "max": 100.0}}},
    }


def _avg():
    return {
        "entry_id": "avg",
        "version_no": 1,
        "entry_type": schema.PART_TYPE_PROCESSING,
        "name": "そのまま",
        "body": {"transfer": "x", "params": {}, "removes": ["gaussian"]},
    }


def _catalog(*entries):
    return {e["entry_id"]: e for e in entries}


def _run(source, signal, params=None, seed=0):
    namespace: dict = {}
    exec(compile(source, "<instrument>", "exec"), namespace)  # テスト内でのみ実行（本番は sandbox）
    return namespace[schema.INSTRUMENT_ENTRYPOINT](signal, params or {}, seed)


class TestValidatePartBody:
    def test_valid_bodies(self):
        assert instrument.validate_part_body(schema.PART_TYPE_INSTRUMENT, _amp()["body"]) == []
        assert instrument.validate_part_body(schema.PART_TYPE_NOISE, _noise()["body"]) == []
        assert instrument.validate_part_body(schema.PART_TYPE_PROCESSING, _avg()["body"]) == []

    def test_unknown_entry_type(self):
        facts = instrument.validate_part_body("apparatus", {})
        assert facts and "実験機の部品ではありません" in facts[0]

    def test_missing_required(self):
        facts = instrument.validate_part_body(schema.PART_TYPE_INSTRUMENT, {"params": {}})
        assert any("transfer" in f for f in facts)
        facts = instrument.validate_part_body(schema.PART_TYPE_NOISE, {})
        assert any("noise_kind" in f for f in facts)

    def test_transfer_rejected_by_ast_guard(self):
        facts = instrument.validate_part_body(
            schema.PART_TYPE_INSTRUMENT, {"transfer": "__import__('os').system('ls')"}
        )
        assert any("許可されていない" in f for f in facts)

    def test_unknown_noise_kind(self):
        facts = instrument.validate_part_body(schema.PART_TYPE_NOISE, {"noise_kind": "pink", "params": {}})
        assert any("noise_kind" in f for f in facts)

    def test_noise_param_declaration_required(self):
        facts = instrument.validate_part_body(schema.PART_TYPE_NOISE, {"noise_kind": "gaussian", "params": {}})
        assert any("sigma" in f for f in facts)

    @pytest.mark.parametrize(
        "params",
        [
            "gain",
            {"gain": 1.0},
            {"gain": {"default": "a"}},
            {"gain": {"default": True}},
            {"gain": {"default": 1.0, "min": "x"}},
            {"gain": {"default": 5.0, "min": 0.0, "max": 1.0}},
            {"gain": {"default": -1.0, "min": 0.0}},
            {"gain": {"default": 0.5, "min": 1.0, "max": 0.0}},
        ],
    )
    def test_bad_params(self, params):
        facts = instrument.validate_part_body(
            schema.PART_TYPE_INSTRUMENT, {"transfer": "x", "params": params}
        )
        assert facts


class TestValidateWiring:
    def test_valid_chain(self):
        cat = _catalog(_amp(), _noise())
        parts = [
            {"slot": "amp", "entry_id": "amp", "version_no": 1, "params": {"gain": 3.0}},
            {"slot": "noise", "entry_id": "noise_gaussian", "version_no": 1, "params": {}},
        ]
        edges = [{"from_slot": "amp", "to_slot": "noise"}]
        assert instrument.validate_wiring(parts, edges, cat) == []
        assert instrument.chain_order(parts, edges) == ["amp", "noise"]

    def test_unknown_entry(self):
        facts = instrument.validate_wiring([{"slot": "a", "entry_id": "nope", "params": {}}], [], {})
        assert any("目録にありません" in f for f in facts)

    def test_param_out_of_range(self):
        cat = _catalog(_amp())
        facts = instrument.validate_wiring(
            [{"slot": "a", "entry_id": "amp", "version_no": 1, "params": {"gain": 99.0}}], [], cat
        )
        assert any("上限" in f for f in facts)

    def test_undeclared_param(self):
        cat = _catalog(_amp())
        facts = instrument.validate_wiring(
            [{"slot": "a", "entry_id": "amp", "params": {"bias": 1.0}}], [], cat
        )
        assert any("宣言されていません" in f for f in facts)

    def test_duplicate_slot(self):
        cat = _catalog(_amp())
        parts = [{"slot": "a", "entry_id": "amp"}, {"slot": "a", "entry_id": "amp"}]
        assert any("重複" in f for f in instrument.validate_wiring(parts, [], cat))

    def test_branching_rejected(self):
        cat = _catalog(_amp(), _noise(), _avg())
        parts = [
            {"slot": "a", "entry_id": "amp"},
            {"slot": "b", "entry_id": "noise_gaussian"},
            {"slot": "c", "entry_id": "avg"},
        ]
        edges = [{"from_slot": "a", "to_slot": "b"}, {"from_slot": "a", "to_slot": "c"}]
        assert instrument.validate_wiring(parts, edges, cat) == [
            "配線は一本の信号経路にしてください（分岐は v1 非対応）。"
        ]

    def test_disconnected_rejected(self):
        cat = _catalog(_amp(), _avg())
        parts = [{"slot": "a", "entry_id": "amp"}, {"slot": "c", "entry_id": "avg"}]
        assert instrument.validate_wiring(parts, [], cat)

    def test_cycle_rejected(self):
        cat = _catalog(_amp(), _avg())
        parts = [{"slot": "a", "entry_id": "amp"}, {"slot": "c", "entry_id": "avg"}]
        edges = [{"from_slot": "a", "to_slot": "c"}, {"from_slot": "c", "to_slot": "a"}]
        facts = instrument.validate_wiring(parts, edges, cat)
        assert facts and "循環" in facts[0]

    def test_edge_to_missing_slot(self):
        cat = _catalog(_amp())
        facts = instrument.validate_wiring(
            [{"slot": "a", "entry_id": "amp"}], [{"from_slot": "a", "to_slot": "z"}], cat
        )
        assert any("存在しない slot" in f for f in facts)

    def test_empty(self):
        assert instrument.validate_wiring([], [], {})


class TestComposeInstrument:
    def _linear(self, sigma):
        cat = _catalog(_amp(gain=2.0), _noise(default=sigma))
        parts = [
            {"slot": "amp", "entry_id": "amp", "version_no": 1, "params": {}},
            {"slot": "noise", "entry_id": "noise_gaussian", "version_no": 1, "params": {}},
        ]
        edges = [{"from_slot": "amp", "to_slot": "noise"}]
        return instrument.compose_instrument(parts, edges, cat)

    def test_source_compiles_and_passes_program_guard(self):
        from core.simulation_bridge import ast_guard

        source = self._linear(0.0)
        compile(source, "<instrument>", "exec")
        result = ast_guard.check_program(source, entrypoint=schema.INSTRUMENT_ENTRYPOINT)
        assert result.ok, result.reasons
        assert result.entrypoint_found

    def test_linear_amplifier_without_noise(self):
        out = _run(self._linear(0.0), {"y": 1.5})
        assert out["chain"] == ["amp", "noise"]
        obs = out["observed"]["y"]
        assert obs["mean"] == pytest.approx(3.0)
        assert obs["std"] == pytest.approx(0.0)
        assert len(obs["samples"]) == instrument.MAX_SAMPLES

    def test_gaussian_noise_gives_spread_and_is_seed_deterministic(self):
        source = self._linear(0.5)
        a = _run(source, {"y": 1.5}, seed=7)
        b = _run(source, {"y": 1.5}, seed=7)
        c = _run(source, {"y": 1.5}, seed=8)
        assert a == b
        assert a != c
        assert a["observed"]["y"]["std"] > 0
        assert a["observed"]["y"]["mean"] == pytest.approx(3.0, abs=0.3)

    def test_runtime_params_override_defaults(self):
        out = _run(self._linear(0.0), {"y": 1.0}, params={"amp": {"gain": 5.0}, "_repeats": 3})
        assert out["observed"]["y"]["mean"] == pytest.approx(5.0)
        assert len(out["observed"]["y"]["samples"]) == 3

    def test_list_signal(self):
        out = _run(self._linear(0.0), {"y": [1.0, 2.0, 3.0]}, params={"_repeats": 2})
        assert out["observed"]["y"]["mean"] == pytest.approx(4.0)

    @pytest.mark.parametrize(
        "kind,param",
        [("poisson", "scale"), ("one_over_f", "amplitude"), ("drift", "rate"), ("offset", "offset")],
    )
    def test_each_noise_template_runs(self, kind, param):
        cat = _catalog(_amp(), _noise(kind=kind, param=param, default=1.0))
        parts = [{"slot": "amp", "entry_id": "amp"}, {"slot": "n", "entry_id": f"noise_{kind}"}]
        edges = [{"from_slot": "amp", "to_slot": "n"}]
        source = instrument.compose_instrument(parts, edges, cat)
        out = _run(source, {"y": [1.0, 2.0, 3.0, 4.0]}, params={"_repeats": 4})
        assert set(out["observed"]["y"]) == {"mean", "std", "samples"}

    def test_offset_template_value(self):
        cat = _catalog(_noise(kind="offset", param="offset", default=0.25))
        source = instrument.compose_instrument([{"slot": "n", "entry_id": "noise_offset"}], [], cat)
        assert _run(source, {"y": 1.0})["observed"]["y"]["mean"] == pytest.approx(1.25)

    def test_invalid_wiring_raises(self):
        with pytest.raises(ValueError):
            instrument.compose_instrument([{"slot": "a", "entry_id": "nope"}], [], {})

    def test_forbidden_transfer_never_inserted(self):
        bad = _amp()
        bad["body"]["transfer"] = "open('x')"
        with pytest.raises(ValueError):
            instrument.compose_instrument([{"slot": "a", "entry_id": "amp"}], [], _catalog(bad))

    def test_deterministic_source(self):
        assert self._linear(0.1) == self._linear(0.1)

    def test_default_params(self):
        cat = _catalog(_amp(gain=2.0))
        assert instrument.default_params([{"slot": "a", "entry_id": "amp", "params": {"gain": 4}}], cat) == {
            "a": {"gain": 4.0}
        }
