"""実験機シミュレータの配線検査と合成（SB層 §5.2・決定論・非LLM・DB 非接触）。

部品（L層 ``library_entries`` の凍結版 body）を信号の流れ（``edges``）に沿って並べ、
1 つの Python 関数 ``simulate_instrument(signal, params, seed)`` のソースを**決定論で**組み立てる。

- 部品の ``transfer`` 式は ``ast_guard.check_expression`` を通ったものだけを埋め込む（SB3）。
- ノイズ源のコードは部品側に持たせず、``noise_kind`` ごとのサーバ側テンプレートで書く。
- 合成したソースはこのプロセスで実行しない。実行は sandbox（``sim-runner``）の責務（SB3）。

設計: ``docs/features/simulation_bridge_design.md`` §4.1 / §5.2。
"""

from __future__ import annotations

import math
import re
from typing import Any

from . import schema

__all__ = [
    "NOISE_PARAM_BY_KIND",
    "DEFAULT_REPEATS",
    "MAX_REPEATS",
    "MAX_SAMPLES",
    "validate_part_body",
    "validate_wiring",
    "chain_order",
    "default_params",
    "compose_instrument",
]

#: ノイズ種別ごとにテンプレートが読むパラメータ名（部品 ``params`` に宣言が要る）。
NOISE_PARAM_BY_KIND: dict[str, str] = {
    schema.NOISE_KIND_GAUSSIAN: "sigma",
    schema.NOISE_KIND_POISSON: "scale",
    schema.NOISE_KIND_ONE_OVER_F: "amplitude",
    schema.NOISE_KIND_DRIFT: "rate",
    schema.NOISE_KIND_OFFSET: "offset",
}

#: 模擬観測のアンサンブル回数（``params["_repeats"]`` で上書き可・上限あり）。
DEFAULT_REPEATS = 64
MAX_REPEATS = 4096
#: 結果に残すサンプル数の上限。
MAX_SAMPLES = 64
#: 1/f ノイズを近似する正弦波の本数。
_ONE_OVER_F_COMPONENTS = 8

_SLOT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_BRANCH_FACT = "配線は一本の信号経路にしてください（分岐は v1 非対応）。"


def _is_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _check_expression(expr: str):
    from . import ast_guard  # 並行実装のため遅延 import

    return ast_guard.check_expression(expr)


def _validate_params_decl(params: Any, *, where: str) -> list[str]:
    facts: list[str] = []
    if not isinstance(params, dict):
        return [f"{where}の params は「名前 → 設定」の対応表にしてください。"]
    for name, spec in params.items():
        label = f"{where}のパラメータ『{name}』"
        if not isinstance(name, str) or not name:
            facts.append(f"{where}のパラメータ名は文字列にしてください。")
            continue
        if not isinstance(spec, dict):
            facts.append(f"{label}は default を持つ対応表にしてください。")
            continue
        if not _is_number(spec.get("default")):
            facts.append(f"{label}の default が数値ではありません。")
            continue
        default = float(spec["default"])
        bounds: dict[str, float] = {}
        for key in ("min", "max"):
            if key in spec and spec[key] is not None:
                if not _is_number(spec[key]):
                    facts.append(f"{label}の {key} が数値ではありません。")
                else:
                    bounds[key] = float(spec[key])
        if "min" in bounds and "max" in bounds and bounds["min"] > bounds["max"]:
            facts.append(f"{label}の min が max より大きくなっています。")
        if "min" in bounds and default < bounds["min"]:
            facts.append(f"{label}の default が min を下回っています。")
        if "max" in bounds and default > bounds["max"]:
            facts.append(f"{label}の default が max を上回っています。")
    return facts


def validate_part_body(entry_type: str, body: dict) -> list[str]:
    """部品の body が §4.1 の規約を満たすか。問題を日本語の事実文で返す（空なら配線できる）。"""
    if entry_type not in schema.PART_TYPES:
        return ["この部品の種類は実験機の部品ではありません（装置モデル・ノイズ源・信号処理の工夫のいずれかにしてください）。"]
    type_label = schema.PART_TYPE_LABELS.get(entry_type, entry_type)
    if not isinstance(body, dict):
        return [f"{type_label}の本文が対応表ではありません。"]
    facts: list[str] = []
    for key in schema.PART_BODY_REQUIRED.get(entry_type, ()):
        if key not in body or body.get(key) in (None, ""):
            facts.append(f"{type_label}に必須の項目『{key}』がありません。")
    if "transfer" in schema.PART_BODY_REQUIRED.get(entry_type, ()) and body.get("transfer"):
        transfer = body.get("transfer")
        if not isinstance(transfer, str):
            facts.append(f"{type_label}の transfer は式の文字列にしてください。")
        else:
            result = _check_expression(transfer)
            if not result.ok:
                facts.append(f"{type_label}の transfer 式が許可されていない書き方を含んでいます。")
                facts.extend(str(r) for r in result.reasons)
    if entry_type == schema.PART_TYPE_NOISE and body.get("noise_kind") not in (None, ""):
        kind = body.get("noise_kind")
        if kind not in schema.NOISE_KINDS:
            facts.append("ノイズ源の noise_kind が既知の種類（gaussian / poisson / one_over_f / drift / offset）ではありません。")
    params = body.get("params")
    if params is not None:
        facts.extend(_validate_params_decl(params, where=type_label))
    if entry_type == schema.PART_TYPE_NOISE and body.get("noise_kind") in NOISE_PARAM_BY_KIND:
        needed = NOISE_PARAM_BY_KIND[body["noise_kind"]]
        if not isinstance(params, dict) or needed not in params:
            facts.append(f"ノイズ源（{body['noise_kind']}）にはパラメータ『{needed}』の宣言が要ります。")
    for key in ("inputs", "outputs", "removes"):
        value = body.get(key)
        if value is not None and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            facts.append(f"{type_label}の {key} は文字列の一覧にしてください。")
    return facts


def chain_order(parts: list[dict], edges: list[dict]) -> list[str] | None:
    """一本の信号経路なら slot を上流から並べて返す。分岐・循環・分断なら None。"""
    slots = [str(p.get("slot") or "") for p in parts]
    if not slots or len(set(slots)) != len(slots):
        return None
    nxt: dict[str, str] = {}
    prev: dict[str, str] = {}
    for edge in edges:
        a = str(edge.get("from_slot") or "")
        b = str(edge.get("to_slot") or "")
        if a not in slots or b not in slots or a == b:
            return None
        if a in nxt or b in prev:
            return None
        nxt[a] = b
        prev[b] = a
    starts = [s for s in slots if s not in prev]
    if len(starts) != 1:
        return None
    order = [starts[0]]
    seen = {starts[0]}
    while order[-1] in nxt:
        following = nxt[order[-1]]
        if following in seen:
            return None
        order.append(following)
        seen.add(following)
    if len(order) != len(slots):
        return None
    return order


def _has_cycle(slots: list[str], edges: list[dict]) -> bool:
    adjacency: dict[str, list[str]] = {s: [] for s in slots}
    for edge in edges:
        a, b = str(edge.get("from_slot") or ""), str(edge.get("to_slot") or "")
        if a in adjacency and b in adjacency:
            adjacency[a].append(b)
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        state[node] = 1
        for nb in adjacency[node]:
            if state.get(nb) == 1:
                return True
            if state.get(nb) is None and visit(nb):
                return True
        state[node] = 2
        return False

    return any(state.get(s) is None and visit(s) for s in slots)


def validate_wiring(parts: list[dict], edges: list[dict], catalog: dict[str, dict]) -> list[str]:
    """配線を検査する。問題を日本語の事実文で返す（空なら合成できる）。

    ``catalog`` は ``entry_id -> {entry_id, version_no, entry_type, name, body}``（凍結版の中身）。
    """
    facts: list[str] = []
    if not isinstance(parts, list) or not parts:
        return ["配線に部品がありません。"]
    if not isinstance(edges, list):
        return ["配線の edges は一覧にしてください。"]
    slots: list[str] = []
    for index, part in enumerate(parts):
        if not isinstance(part, dict):
            facts.append(f"{index + 1} 番目の部品の指定が対応表ではありません。")
            continue
        slot = str(part.get("slot") or "")
        if not _SLOT_RE.match(slot):
            facts.append(f"{index + 1} 番目の部品の slot 名は英字で始まる英数字と _ にしてください。")
        elif slot in slots:
            facts.append(f"slot『{slot}』が重複しています。")
        slots.append(slot)
        entry_id = str(part.get("entry_id") or "")
        entry = catalog.get(entry_id)
        if entry is None:
            facts.append(f"slot『{slot}』の部品は凍結された部品の目録にありません。")
            continue
        version_no = part.get("version_no")
        if version_no is not None and entry.get("version_no") is not None and version_no != entry.get("version_no"):
            facts.append(f"slot『{slot}』の部品の版が目録の凍結版と一致しません。")
        entry_type = str(entry.get("entry_type") or "")
        if entry_type not in schema.PART_TYPES:
            facts.append(f"slot『{slot}』の部品は実験機の部品ではありません。")
            continue
        body = entry.get("body") if isinstance(entry.get("body"), dict) else {}
        body_facts = validate_part_body(entry_type, body)
        if body_facts:
            facts.append(f"slot『{slot}』の部品の本文が規約を満たしていません。")
            facts.extend(body_facts)
        declared = body.get("params") if isinstance(body.get("params"), dict) else {}
        overrides = part.get("params") or {}
        if not isinstance(overrides, dict):
            facts.append(f"slot『{slot}』の params は「名前 → 値」の対応表にしてください。")
            continue
        for name, value in overrides.items():
            spec = declared.get(name)
            if not isinstance(spec, dict):
                facts.append(f"slot『{slot}』のパラメータ『{name}』は部品に宣言されていません。")
                continue
            if not _is_number(value):
                facts.append(f"slot『{slot}』のパラメータ『{name}』の値が数値ではありません。")
                continue
            if _is_number(spec.get("min")) and float(value) < float(spec["min"]):
                facts.append(f"slot『{slot}』のパラメータ『{name}』が部品の下限を下回っています。")
            if _is_number(spec.get("max")) and float(value) > float(spec["max"]):
                facts.append(f"slot『{slot}』のパラメータ『{name}』が部品の上限を上回っています。")
    for edge in edges:
        if not isinstance(edge, dict):
            facts.append("配線の辺の指定が対応表ではありません。")
            continue
        a, b = str(edge.get("from_slot") or ""), str(edge.get("to_slot") or "")
        if a not in slots or b not in slots:
            facts.append("配線の辺が存在しない slot を指しています。")
        elif a == b:
            facts.append("配線の辺が同じ slot に戻っています。")
    if facts:
        return facts
    if _has_cycle(slots, edges):
        return ["配線が循環しています。信号は上流から下流へ一方向に流してください。"]
    if chain_order(parts, edges) is None:
        return [_BRANCH_FACT]
    return []


def default_params(parts: list[dict], catalog: dict[str, dict]) -> dict[str, dict[str, float]]:
    """slot ごとに「部品の default ← 配線の params」を重ねた値を返す。"""
    resolved: dict[str, dict[str, float]] = {}
    for part in parts:
        slot = str(part.get("slot") or "")
        entry = catalog.get(str(part.get("entry_id") or "")) or {}
        body = entry.get("body") if isinstance(entry.get("body"), dict) else {}
        declared = body.get("params") if isinstance(body.get("params"), dict) else {}
        values: dict[str, float] = {}
        for name, spec in declared.items():
            if isinstance(spec, dict) and _is_number(spec.get("default")):
                values[str(name)] = float(spec["default"])
        for name, value in (part.get("params") or {}).items():
            if _is_number(value):
                values[str(name)] = float(value)
        resolved[slot] = values
    return resolved


def _noise_lines(kind: str, indent: str) -> list[str]:
    param = NOISE_PARAM_BY_KIND[kind]
    get = f'float(params.get({param!r}, 0.0))'
    if kind == schema.NOISE_KIND_GAUSSIAN:
        return [f"{indent}x = x + rng.normal(0.0, abs({get}), size=np.shape(x))"]
    if kind == schema.NOISE_KIND_POISSON:
        return [
            f"{indent}scale = float(params.get('scale', 1.0))",
            f"{indent}if scale > 0:",
            f"{indent}    x = rng.poisson(np.clip(x, 0.0, None) * scale) / scale",
        ]
    if kind == schema.NOISE_KIND_ONE_OVER_F:
        return [
            f"{indent}n = int(np.size(x))",
            f"{indent}if n > 0:",
            f"{indent}    amplitude = {get}",
            f"{indent}    t = np.arange(n, dtype=float)",
            f"{indent}    noise = np.zeros(n)",
            f"{indent}    for k in range(1, {_ONE_OVER_F_COMPONENTS + 1}):",
            f"{indent}        phase = rng.uniform(0.0, 2.0 * math.pi)",
            f"{indent}        noise = noise + (amplitude / k) * np.sin(2.0 * math.pi * k * t / n + phase)",
            f"{indent}    x = x + np.reshape(noise, np.shape(x))",
        ]
    if kind == schema.NOISE_KIND_DRIFT:
        return [
            f"{indent}n = int(np.size(x))",
            f"{indent}if n > 0:",
            f"{indent}    x = x + {get} * np.reshape(np.arange(n, dtype=float) / max(n, 1), np.shape(x))",
        ]
    if kind == schema.NOISE_KIND_OFFSET:
        return [f"{indent}x = x + {get}"]
    raise ValueError(f"未知のノイズ種別です: {kind}")


def compose_instrument(
    parts: list[dict],
    edges: list[dict],
    catalog: dict[str, dict],
    *,
    entrypoint: str = schema.INSTRUMENT_ENTRYPOINT,
) -> str:
    """配線から ``simulate_instrument(signal, params, seed)`` の Python ソースを決定論で合成する。

    ``signal`` は理論プログラムの出力（記号 → 数値 / 数値の一覧）、``params`` は
    ``{slot: {param: value}}``（部品の default と配線の params に重ねる）と任意の ``_repeats``。
    戻り値は ``{"observed": {記号: {"mean", "std", "samples"}}, "chain": [slot, ...]}``。

    配線が検査を通らない・transfer 式が許可リスト外なら ``ValueError``（事実文を連結）。
    """
    problems = validate_wiring(parts, edges, catalog)
    if problems:
        raise ValueError(" ".join(problems))
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", entrypoint or ""):
        raise ValueError("実験機プログラムの関数名が不正です。")
    order = chain_order(parts, edges) or []
    by_slot = {str(p.get("slot")): p for p in parts}
    defaults = default_params(parts, catalog)

    stage_lines: list[str] = []
    indent = " " * 12
    for slot in order:
        part = by_slot[slot]
        entry = catalog[str(part.get("entry_id"))]
        entry_type = entry.get("entry_type")
        body = entry.get("body") or {}
        stage_lines.append(f"{indent}params = merged[{slot!r}]")
        if entry_type == schema.PART_TYPE_NOISE:
            stage_lines.extend(_noise_lines(str(body.get("noise_kind")), indent))
        else:
            transfer = str(body.get("transfer") or "")
            check = _check_expression(transfer)
            if not check.ok:
                raise ValueError("transfer 式が許可されていない書き方を含んでいます: " + " ".join(check.reasons))
            if "\n" in transfer or "\r" in transfer:
                raise ValueError("transfer 式は 1 行にしてください。")
            stage_lines.append(f"{indent}x = np.asarray(({transfer}), dtype=float)")

    lines = [
        "import math",
        "import numpy as np",
        "",
        "",
        f"def {entrypoint}(signal, params, seed):",
        "    overrides = params if isinstance(params, dict) else {}",
        f"    merged = {defaults!r}",
        "    for slot_name in list(merged.keys()):",
        "        extra = overrides.get(slot_name)",
        "        if isinstance(extra, dict):",
        "            for key, value in extra.items():",
        "                merged[slot_name][key] = float(value)",
        f"    repeats = int(overrides.get('_repeats', {DEFAULT_REPEATS}))",
        f"    repeats = max(1, min(repeats, {MAX_REPEATS}))",
        "    rng = np.random.default_rng(seed)",
        "    observed = {}",
        "    for symbol in sorted((signal or {}).keys()):",
        "        base = np.asarray(signal[symbol], dtype=float)",
        "        values = []",
        "        for _ in range(repeats):",
        "            x = np.array(base, dtype=float)",
        *stage_lines,
        "            values.append(float(np.mean(x)))",
        "        arr = np.asarray(values, dtype=float)",
        "        std = float(np.std(arr, ddof=1)) if arr.size > 1 else 0.0",
        "        observed[symbol] = {",
        "            'mean': float(np.mean(arr)),",
        "            'std': std,",
        f"            'samples': [float(v) for v in arr[:{MAX_SAMPLES}]],",
        "        }",
        f"    return {{'observed': observed, 'chain': {list(order)!r}}}",
        "",
    ]
    return "\n".join(lines)
