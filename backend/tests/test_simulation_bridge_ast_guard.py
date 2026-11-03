"""SB層 ast_guard（SB3）— 生成コード・部品式の AST 許可リスト検査。"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402

from core.simulation_bridge import ast_guard, schema  # noqa: E402

GOOD = '''import math
import numpy as np


def _helper(x):
    return np.exp(-x)


def simulate(inputs, params):
    x = np.asarray(inputs.get("x", [0.0, 1.0]), dtype=float)
    gain = params.get("gain", 1.0)
    try:
        y = gain * _helper(x) + math.sqrt(2.0)
    except ZeroDivisionError:
        y = x
    f = lambda v: v * 2
    return {"y": f(y)}
'''


def _wrap(body: str) -> str:
    return f"def simulate(inputs, params):\n    {body}\n    return {{}}\n"


class TestProgram:
    def test_accepts_legit_numpy_program(self):
        result = ast_guard.check_program(GOOD)
        assert result.ok, result.reasons
        assert result.entrypoint_found

    @pytest.mark.parametrize(
        "source, needle",
        [
            (_wrap("open('f')"), "open"),
            (_wrap("exec('1')"), "exec"),
            (_wrap("t = ().__class__"), "__class__"),
            ("import os\n" + _wrap("pass"), "os"),
            ("from numpy import *\n" + _wrap("pass"), "*"),
            ("class A:\n    pass\n" + _wrap("pass"), "A"),
            (_wrap("g = getattr(inputs, 'x')"), "getattr"),
            (_wrap("np.load('x')"), "load"),
            ("import subprocess\n" + _wrap("pass"), "subprocess"),
            (_wrap("with x as f:\n        pass"), "with"),
        ],
    )
    def test_rejects(self, source, needle):
        result = ast_guard.check_program(source)
        assert not result.ok
        assert any(needle in r for r in result.reasons), result.reasons

    def test_rejects_over_length(self):
        source = _wrap("pass") + "\n".join(f"x{i} = {i}" for i in range(schema.PROGRAM_MAX_LINES + 1))
        result = ast_guard.check_program(source)
        assert not result.ok
        assert any("行数" in r for r in result.reasons)

    def test_syntax_error(self):
        result = ast_guard.check_program("def simulate(:\n")
        assert not result.ok
        assert not result.entrypoint_found

    def test_entrypoint_needs_two_params_and_top_level(self):
        assert not ast_guard.check_program("def simulate(x):\n    return {}\n").entrypoint_found
        nested = "def outer(a, b):\n    def simulate(i, p):\n        return {}\n    return {}\n"
        assert not ast_guard.check_program(nested).entrypoint_found
        res = ast_guard.check_program(
            "def simulate_instrument(s, p, seed):\n    return {}\n", entrypoint=schema.INSTRUMENT_ENTRYPOINT
        )
        assert res.ok and res.entrypoint_found

    def test_async_and_global_rejected(self):
        assert not ast_guard.check_program("async def simulate(i, p):\n    return {}\n").ok
        assert not ast_guard.check_program("X = 1\n" + _wrap("global X")).ok

    def test_dunder_string_rejected(self):
        assert not ast_guard.check_program(_wrap("k = '__subclasses__'")).ok


class TestExpression:
    @pytest.mark.parametrize(
        "expr",
        ['params["gain"] * np.exp(-x)', "x + params['offset']", "math.sin(x) * np.sqrt(params['k'])", "np.random.normal(x)"],
    )
    def test_accepts(self, expr):
        result = ast_guard.check_expression(expr)
        assert result.ok, result.reasons

    @pytest.mark.parametrize(
        "expr",
        [
            '__import__("os")',
            "x.__class__",
            'open("f")',
            "(lambda v: v)(x)",
            "[v for v in x]",
            "y + 1",
            "abs(x)",
            "np.load('f')",
            "x[0]",
        ],
    )
    def test_rejects(self, expr):
        result = ast_guard.check_expression(expr)
        assert not result.ok

    def test_empty_and_syntax(self):
        assert not ast_guard.check_expression("").ok
        assert not ast_guard.check_expression("x +").ok


def test_module_is_pure():
    import ast

    src = (BACKEND_DIR / "core" / "simulation_bridge" / "ast_guard.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"exec", "eval", "compile"}
    assert imported <= {"__future__", "ast", "core.simulation_bridge"}, imported
