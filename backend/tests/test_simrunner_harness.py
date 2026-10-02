"""simrunner/server.py の harness（§5.5）— HTTP を介さず run_source を直接呼ぶ（子プロセスは実 python）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("resource")
pytest.importorskip("numpy")

SERVER = Path(__file__).resolve().parents[2] / "simrunner" / "server.py"


@pytest.fixture(scope="module")
def server():
    spec = importlib.util.spec_from_file_location("simrunner_server_under_test", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NUMPY_PROGRAM = '''import numpy as np
import math

def simulate(inputs, params):
    x = np.linspace(0.0, 1.0, 5) * params.get("gain", 1.0)
    return {"y": x, "total": float(np.sum(x)), "noise": rng.normal(), "bad": float("nan"), "c": complex(1, 2)}
'''


def test_numpy_program_runs(server):
    out = server.run_source({"source": NUMPY_PROGRAM, "entrypoint": "simulate", "inputs": {}, "params": {"gain": 2.0}, "seed": 1, "timeout_s": 10})
    assert out["ok"] is True, out
    assert out["outputs"]["y"] == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert out["outputs"]["total"] == 5.0
    assert out["outputs"]["bad"] is None
    assert out["outputs"]["c"] == {"real": 1.0, "imag": 2.0}
    again = server.run_source({"source": NUMPY_PROGRAM, "params": {"gain": 2.0}, "seed": 1, "timeout_s": 10})
    assert again["outputs"]["noise"] == out["outputs"]["noise"]


def test_open_is_unavailable(server):
    src = "def simulate(inputs, params):\n    return {'t': open('/etc/hosts').read()}\n"
    out = server.run_source({"source": src, "timeout_s": 10})
    assert out["ok"] is False
    assert "open" in out["error"]


def test_import_os_and_dunder_are_blocked(server):
    out = server.run_source({"source": "import os\ndef simulate(i, p):\n    return {}\n", "timeout_s": 10})
    assert out["ok"] is False and "ImportError" in out["error"]
    out = server.run_source({"source": "def simulate(i, p):\n    return {'k': ().__class__}\n", "timeout_s": 10})
    assert out["ok"] is False and "PermissionError" in out["error"]


def test_instrument_three_arg_call_and_truncation(server):
    src = (
        "import numpy as np\n"
        "def simulate_instrument(signal, params, seed):\n"
        "    return {'seed': seed, 'big': np.zeros(10000)}\n"
    )
    out = server.run_source({"source": src, "entrypoint": "simulate_instrument", "seed": 5, "timeout_s": 10})
    assert out["ok"] is True, out
    assert out["outputs"]["seed"] == 5
    assert len(out["outputs"]["big"]) == 4096


def test_timeout(server):
    out = server.run_source({"source": "def simulate(i, p):\n    while True:\n        pass\n", "timeout_s": 1})
    assert out["ok"] is False


def test_missing_source(server):
    assert server.run_source({})["ok"] is False
