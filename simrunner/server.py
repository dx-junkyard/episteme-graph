"""sim-runner — 検証シミュレーション層（SB層）の隔離実行サーバ（stdlib のみ）。

設計: docs/features/simulation_bridge_design.md §5.5（SB3）。
POST /run {source, entrypoint, inputs, params, seed, timeout_s} → 子プロセス
``python -I -S -B -c <HARNESS>`` に stdin で渡して実行し、1 行の JSON を返す。
ディスクに何も書かない・子プロセスへ環境変数を渡さない・資源上限（resource）を掛ける。
テストは HTTP を介さず :func:`run_source` を直接呼ぶ。
"""

from __future__ import annotations

import json
import os
import site
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY_BYTES = 1_000_000
MAX_TIMEOUT_S = 60.0
MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
ERROR_MAX_CHARS = 500
STDERR_TAIL_CHARS = 1000

HARNESS = r'''
import json, math, sys
payload = json.loads(sys.stdin.read())
for p in payload.get("lib_paths") or []:
    if p not in sys.path:
        sys.path.append(p)
try:
    import numpy
except Exception:
    numpy = None
import ast, builtins, inspect
ALLOWED = {"math", "numpy", "cmath"}
_real_import = builtins.__import__
def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name.split(".")[0] not in ALLOWED:
        raise ImportError("import は math / numpy / cmath だけが許可されています: " + str(name))
    return _real_import(name, globals, locals, fromlist, level)
SAFE = {k: getattr(builtins, k) for k in (
    "abs", "min", "max", "sum", "range", "len", "enumerate", "zip", "map", "filter", "float",
    "int", "bool", "str", "list", "dict", "tuple", "set", "round", "pow", "sorted", "reversed",
    "isinstance", "complex", "ValueError", "ZeroDivisionError", "Exception", "True", "False", "None")}
SAFE["__import__"] = _guarded_import
LIMIT = 4096
def conv(v):
    if numpy is not None:
        if isinstance(v, numpy.ndarray):
            flat = v.ravel()[:LIMIT] if v.size > LIMIT else v
            return conv(flat.tolist())
        if isinstance(v, numpy.generic):
            return conv(v.item())
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, complex):
        return {"real": conv(v.real), "imag": conv(v.imag)}
    if isinstance(v, dict):
        return {str(k): conv(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [conv(x) for x in list(v)[:LIMIT]]
    return str(v)[:200]
def main():
    source = payload.get("source") or ""
    entry = payload.get("entrypoint") or "simulate"
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise PermissionError("特殊属性へのアクセスは許可されていません: " + node.attr)
    ns = {"__builtins__": SAFE, "__name__": "__sim__"}
    seed = payload.get("seed")
    if numpy is not None:
        ns["rng"] = numpy.random.default_rng(seed)
    exec(compile(tree, "<simulation>", "exec"), ns)
    fn = ns.get(entry)
    if not callable(fn):
        raise NameError("入口関数がありません: " + entry)
    inputs, params = payload.get("inputs") or {}, payload.get("params") or {}
    try:
        n_params = len([p for p in inspect.signature(fn).parameters.values()
                        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)])
    except (TypeError, ValueError):
        n_params = 2
    result = fn(inputs, params, seed) if n_params >= 3 else fn(inputs, params)
    out = conv(result)
    return out if isinstance(out, dict) else {"output": out}
try:
    line = {"ok": True, "outputs": main()}
except BaseException as exc:
    line = {"ok": False, "error": (type(exc).__name__ + ": " + str(exc))[:500]}
sys.stdout.write(json.dumps(line, ensure_ascii=False, allow_nan=False) + "\n")
sys.stdout.flush()
'''


def _lib_paths() -> list[str]:
    """子プロセス（-S で site を読まない）に numpy の在処だけを教える。"""
    paths: list[str] = []
    try:
        candidates = list(site.getsitepackages())
    except Exception:  # noqa: BLE001
        candidates = []
    candidates.append("/opt/simlibs")
    for path in candidates:
        if os.path.isdir(path) and path not in paths:
            paths.append(path)
    return paths


def _limits(timeout_s: float):
    def apply() -> None:
        try:
            import resource
        except ImportError:  # pragma: no cover — 非 POSIX
            return
        cpu = int(timeout_s) + 1
        for name, value in (
            ("RLIMIT_AS", MEMORY_LIMIT_BYTES),
            ("RLIMIT_CPU", cpu),
            ("RLIMIT_NOFILE", 8),
            ("RLIMIT_FSIZE", 0),
        ):
            limit = getattr(resource, name, None)
            if limit is None:
                continue
            try:
                resource.setrlimit(limit, (value, value))
            except (ValueError, OSError):  # macOS は RLIMIT_AS を拒むことがある
                pass
    return apply


def _clip(text: str, limit: int) -> str:
    return (text or "")[-limit:] if len(text or "") > limit else (text or "")


def run_source(payload: dict) -> dict:
    """1 回の実行。HTTP なしで呼べる純粋な入口（テストはこれを直接呼ぶ）。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("source"), str):
        return {"ok": False, "error": "ValueError: source がありません", "stderr_tail": ""}
    try:
        timeout_s = float(payload.get("timeout_s") or 20)
    except (TypeError, ValueError):
        timeout_s = 20.0
    timeout_s = min(max(timeout_s, 1.0), MAX_TIMEOUT_S)
    child_input = {
        "source": payload.get("source"),
        "entrypoint": str(payload.get("entrypoint") or "simulate"),
        "inputs": payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {},
        "params": payload.get("params") if isinstance(payload.get("params"), dict) else {},
        "seed": payload.get("seed") if isinstance(payload.get("seed"), int) else None,
        "lib_paths": _lib_paths(),
    }
    try:
        proc = subprocess.run(  # noqa: S603 — 実行するのは自分自身の python と固定の harness だけ
            [sys.executable, "-I", "-S", "-B", "-c", HARNESS],
            input=json.dumps(child_input),
            capture_output=True,
            text=True,
            env={},
            cwd="/",
            timeout=timeout_s,
            preexec_fn=_limits(timeout_s),  # noqa: PLW1509
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "TimeoutError: 実行時間の上限を超えました", "stderr_tail": ""}
    stderr_tail = _clip(proc.stderr or "", STDERR_TAIL_CHARS)
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if not lines:
        reason = f"returncode={proc.returncode}"
        return {"ok": False, "error": f"RuntimeError: 実行が結果を返さずに終了しました（{reason}）", "stderr_tail": stderr_tail}
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": "RuntimeError: 実行結果を読めません", "stderr_tail": stderr_tail}
    if not isinstance(result, dict):
        return {"ok": False, "error": "RuntimeError: 実行結果の形が不正です", "stderr_tail": stderr_tail}
    result["error"] = _clip(str(result.get("error") or ""), ERROR_MAX_CHARS)
    result["stderr_tail"] = stderr_tail
    return result


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            self._send(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send(413, {"ok": False, "error": "request body too large or empty"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, {"ok": False, "error": "invalid json"})
            return
        self._send(200, run_source(payload))

    def log_message(self, fmt: str, *args) -> None:  # 本文・コードをログに出さない
        sys.stderr.write("sim-runner: " + (fmt % args) + "\n")


def main() -> None:
    port = int(os.environ.get("SIM_RUNNER_PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()  # noqa: S104 — 内部ネットワーク専用


if __name__ == "__main__":
    main()
