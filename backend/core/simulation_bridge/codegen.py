"""検証シミュレーション層（SB層）— 理論モジュールから理論プログラムを生成する（LLM・候補）。

設計: ``docs/features/simulation_bridge_design.md`` §5.1（SB2 / SB3 / SB9 / SB10）。

- 入力は呼び出し側（API）が取得済みの **素の dict**（理論モジュール DTO・
  ``knowledge_equations_live`` の行・``knowledge_symbols_live`` の行）。本モジュールは DB に触れない。
- LLM は ``structured_fn`` / ``text_fn`` の注入で呼ぶ（テストの差し替え面・U層帰属の
  ``usage_context`` は呼び出し側が張る）。1 コール + 修復 1 回まで（SB9）。
- 生成コードは :func:`core.simulation_bridge.ast_guard.check_program` を通ったときだけ
  ``accepted``。通らなければ実行しない候補として事実文付きで返す（SB3）。
- 式・記号の本文は第三者の論文由来なので untrusted 区画（:data:`UNTRUSTED_SOURCE_NOTICE` +
  ``json.dumps``）で渡す（TB1 / TB2）。
- confidence は持たない（SB6）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from pydantic import BaseModel, Field

from core.llm_worker.single_shot import strip_code_fence, structured_call
from core.simulation_bridge import ast_guard, schema
from core.text_hygiene import UNTRUSTED_SOURCE_NOTICE, sanitize_source_text_for_prompt, strip_control_sequences

__all__ = [
    "FACT_GENERATION_FAILED",
    "build_codegen_prompt",
    "generate_program",
    "program_facts_for_display",
]

#: LLM の応答が得られなかった・読めなかったとき（数値なしの事実文）。
FACT_GENERATION_FAILED = "コードを生成できませんでした（AI の応答を読めませんでした）。"
# io_contract の出力記号が理論モジュールの出力の式の記号と重ならないときの事実文は
# ``schema.outputs_not_matching_fact``（第 21 周 R9: 両方の記号を名指しする・警告で候補のまま）。

_TEXT_LIMIT = 1200
_DEFINITION_LIMIT = 300
_FACT_LIMIT = 300
_MAX_FACTS = 12

_INSTRUCTION = """あなたは理論の数式を、検算用の小さな Python 関数に翻訳する担当です。

# 書くもの
- 純粋な Python 関数 `simulate(inputs: dict, params: dict) -> dict` を 1 つだけ top-level に定義してください。
- 理論モジュールの「入力の式」（role=input / foundation）から「出力の式」（role=output）への計算を実装します。
- 戻り値は、出力の式が定める記号の名前をキーにした dict にしてください。
- import してよいのは `math` / `numpy`（`import numpy as np`）/ `cmath` だけです。
- ファイル・ネットワーク・OS への入出力、`open` / `exec` / `eval` / `__import__` / `getattr`、
  クラス定義、`with` 文、`global`、`__` で始まる属性は使わないでください。
- 乱数が要るときは名前空間に用意される `rng`（numpy の Generator）を使ってください。
- 観測・ノイズ・装置の効果は入れないでください（それは別の部品が受け持ちます）。

# 併せて返すもの
- `io_contract`: `{"inputs": [...], "outputs": [...], "parameters": [...]}`。各要素は
  `{"symbol": 記号, "kind": 種別, "unit": 単位（不明なら空文字）, "default": 既定値（無ければ空文字）}`。
- `facts`: どの近似を置いたか・何を省いたかを日本語の短い事実文で列挙してください。
  式の本文が読み取れない・復元された式で不確かな箇所も、ここに事実として書いてください。
  facts は学習者が読む文です。role= などの内部の語・英語の型名を書かないでください。

# 出力形式
JSON オブジェクト `{"source_code": "...", "io_contract": {...}, "facts": ["..."]}` だけを返してください。"""


class _ProgramResponse(BaseModel):
    source_code: str = ""
    io_contract: dict = Field(default_factory=dict)
    facts: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 入力の整形（純関数）
# ---------------------------------------------------------------------------


def _text(value: Any, limit: int = _TEXT_LIMIT) -> str:
    if value is None:
        return ""
    return sanitize_source_text_for_prompt(str(value))[:limit]


def _payload_dict(row: dict) -> dict:
    payload = row.get("agent_payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            payload = {}
    return payload if isinstance(payload, dict) else {}


def _is_reconstructed(row: dict) -> bool:
    if row.get("reconstructed") is True:
        return True
    policy = _payload_dict(row).get("confidence_policy")
    return bool(isinstance(policy, dict) and policy.get("must_not_treat_as_source_extracted"))


def _equation_id(row: dict) -> str:
    return str(row.get("agent_equation_id") or row.get("equation_id") or row.get("id") or "")


def _symbol_id(row: dict) -> str:
    return str(row.get("id") or row.get("agent_symbol_id") or row.get("canonical_symbol") or "")


def _has_text(row: dict) -> bool:
    return bool(str(row.get("latex") or "").strip() or str(row.get("plain_text") or "").strip())


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return []


def _roles(module: dict) -> dict[str, str]:
    roles: dict[str, str] = {}
    for key, role in (("foundation", "foundation"), ("inputs", "input"), ("outputs", "output")):
        for item in module.get(key) or []:
            if isinstance(item, dict) and item.get("equation_id"):
                roles[str(item["equation_id"])] = role
    return roles


def _prompt_material(module: dict, equations: list[dict], symbols: list[dict]) -> dict:
    roles = _roles(module)
    members = []
    for member in module.get("members") or []:
        if not isinstance(member, dict):
            continue
        members.append(
            {
                "operation": _text(member.get("operation_label"), 120),
                "inputs": [_text(v, 120) for v in _str_list(member.get("input_labels"))],
                "outputs": [_text(v, 120) for v in _str_list(member.get("output_labels"))],
            }
        )
    eq_items = []
    for row in equations:
        if not isinstance(row, dict):
            continue
        eq_items.append(
            {
                "label": _text(row.get("label") or row.get("display_label"), 120),
                "role": roles.get(_equation_id(row), "member"),
                "latex": _text(row.get("latex")),
                "plain_text": _text(row.get("plain_text")),
                "defined_symbols": [_text(v, 60) for v in _str_list(row.get("defined_symbols"))],
                "used_symbols": [_text(v, 60) for v in _str_list(row.get("used_symbols"))],
                "reconstructed": _is_reconstructed(row),
            }
        )
    sym_items = []
    for row in symbols:
        if not isinstance(row, dict):
            continue
        constraints = _payload_dict(row).get("domain_constraints")
        sym_items.append(
            {
                "symbol": _text(row.get("canonical_symbol"), 60),
                "kind": _text(row.get("kind") or "unknown", 30),
                "unit": _text(row.get("unit"), 60),
                "scope": _text(row.get("scope"), 120),
                "definitions": [_text(v, _DEFINITION_LIMIT) for v in _str_list(row.get("definition_evidence_texts"))[:2]],
                "domain_constraints": [_text(v, 200) for v in _str_list(constraints)][:5],
            }
        )
    assumptions = []
    for item in module.get("assumptions") or []:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            assumptions.append(_text(item.get("text"), 400))
    return {
        "module": {
            "label": _text(module.get("label"), 200),
            "theory_object": _text(module.get("theory_object"), 200),
            "assumptions": assumptions,
            "members": members,
        },
        "equations": eq_items,
        "symbols": sym_items,
    }


def build_codegen_prompt(module: dict, *, equations: list[dict], symbols: list[dict]) -> str:
    """指示 → untrusted 区画（notice + JSON）の順に組み立てる（TB1 / TB2）。"""
    material = _prompt_material(module, equations, symbols)
    return (
        _INSTRUCTION
        + "\n\n---\n# 理論モジュールの資料\n"
        + UNTRUSTED_SOURCE_NOTICE
        + "\n```json\n"
        + json.dumps(material, ensure_ascii=False, indent=1)
        + "\n```"
    )


def _repair_prompt(reasons: tuple[str, ...], previous_code: str) -> str:
    lines = "\n".join(f"- {r}" for r in reasons) or "- （理由不明）"
    return (
        "前回のコードは安全性の検査を通りませんでした。見つかった問題:\n"
        f"{lines}\n\n"
        "問題を取り除いた完全なコードを、同じ JSON 形式（source_code / io_contract / facts）で返してください。\n"
        "前回のコード:\n```python\n"
        f"{previous_code[:12000]}\n```"
    )


# ---------------------------------------------------------------------------
# 応答の正規化
# ---------------------------------------------------------------------------


def _as_dict(response: Any) -> dict | None:
    if response is None:
        return None
    if isinstance(response, BaseModel):
        return response.model_dump()
    if isinstance(response, dict):
        return response
    return None


def _contract_items(value: Any) -> list[dict]:
    items: list[dict] = []
    if not isinstance(value, list):
        return items
    for item in value:
        if isinstance(item, str):
            item = {"symbol": item}
        if not isinstance(item, dict):
            continue
        symbol = strip_control_sequences(str(item.get("symbol") or item.get("name") or "")).strip()[:80]
        if not symbol:
            continue
        default = item.get("default", "")
        if isinstance(default, bool) or not isinstance(default, (int, float)):
            default = "" if default is None else strip_control_sequences(str(default))[:80]
        items.append(
            {
                "symbol": symbol,
                "kind": strip_control_sequences(str(item.get("kind") or "")).strip()[:40],
                "unit": strip_control_sequences(str(item.get("unit") or "")).strip()[:60],
                "default": default,
            }
        )
    return items


def _normalize_contract(value: Any) -> dict[str, list[dict]]:
    raw = value if isinstance(value, dict) else {}
    return {key: _contract_items(raw.get(key)) for key in ("inputs", "outputs", "parameters")}


#: 資料の ``role=`` 語彙 → 学習者が読む日本語（第 21 周 R8: LLM の facts がプロンプトの語彙を写す）。
ROLE_WORDS: dict[str, str] = {
    "input": "入力の式",
    "output": "出力の式",
    "member": "手順の中の式",
    "foundation": "共有の基礎の式",
}
_ROLE_RE = re.compile(r"role\s*=\s*(input|output|member|foundation)\b", re.IGNORECASE)


def _humanize_fact(text: str) -> str:
    """``role=output`` 等を日本語に置換する。置換し切れない ``role=`` が残る文は空にする（落とす）。"""
    replaced = _ROLE_RE.sub(lambda m: ROLE_WORDS[m.group(1).lower()], text)
    if re.search(r"role\s*=", replaced, re.IGNORECASE):
        return ""
    return replaced


def _normalize_facts(value: Any) -> list[str]:
    facts: list[str] = []
    for item in _str_list(value):
        text = _humanize_fact(strip_control_sequences(item).strip())[:_FACT_LIMIT]
        if text and text not in facts:
            facts.append(text)
    return facts[:_MAX_FACTS]


def _source_of(data: dict) -> str:
    code = str(data.get("source_code") or "")
    return strip_code_fence(strip_control_sequences(code))


def _output_symbols(module: dict, equations: list[dict]) -> set[str]:
    """モジュールの出力の式が定義する記号名（dict 形のレコードは名前だけを取る — 第 21 周の再演で dict の repr が
    学習者の事実文に出た）。判定は論文層の正本 ``equation_defined_symbols`` に委ねる。"""
    from core.graph_paper_layer.schema import equation_defined_symbols  # 遅延 import（循環回避）

    output_ids = {str(o.get("equation_id")) for o in module.get("outputs") or [] if isinstance(o, dict)}
    symbols: set[str] = set()
    for row in equations:
        if isinstance(row, dict) and _equation_id(row) in output_ids:
            names = equation_defined_symbols(row)
            if not names:
                names = [x for x in _str_list(row.get("defined_symbols")) if not x.lstrip().startswith("{")]
            symbols.update(names)
    return {s.strip() for s in symbols if s.strip() and not s.lstrip().startswith("{")}


_SYMBOL_NORM_RE = re.compile(r"\\(?:mathrm|mathcal|mathbf|mathit|rm|text|mathsf|boldsymbol|bm)\s*\{([^{}]*)\}")


def normalize_symbol(name: Any) -> str:
    """記号名の照合用の正規化（TeX の修飾・波括弧・バックスラッシュ・空白・`$` を落として小文字に）。
    ``C_{\\mathrm{eff}}`` と ``C_eff`` を同じ記号とみなす（第 21 周の再演: ASCII 名と TeX 名の食い違いで誤警告）。"""
    text = str(name or "").replace("$", "")
    for _ in range(3):
        text = _SYMBOL_NORM_RE.sub(r"\1", text)
    text = re.sub(r"\\[A-Za-z]+", lambda m: m.group(0)[1:], text)
    return re.sub(r"[{}\\\s]", "", text).lower()


def symbols_overlap(declared: set[str], expected: set[str]) -> bool:
    d = {normalize_symbol(x) for x in declared if str(x).strip()}
    e = {normalize_symbol(x) for x in expected if str(x).strip()}
    return bool(d & e)


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def generate_program(
    module: dict,
    *,
    equations: list[dict],
    symbols: list[dict],
    structured_fn: Callable[..., Any],
    text_fn: Callable[..., str] | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> schema.ProgramDraft:
    """理論モジュール 1 つから理論プログラムの候補を作る（LLM 1 コール + 修復 1 回まで）。

    式の本文が 1 つも無ければ LLM を呼ばずに事実文だけを返す。戻り値は常に
    :class:`schema.ProgramDraft`（例外を外に出さない）。``accepted`` が真のときだけ実行してよい。
    """
    module = module if isinstance(module, dict) else {}
    equations = [row for row in (equations or []) if isinstance(row, dict)]
    symbols = [row for row in (symbols or []) if isinstance(row, dict)]
    reconstructed = any(_is_reconstructed(row) for row in equations if _has_text(row))
    provenance: dict[str, Any] = {
        "model": model or "",
        "equation_ids": [eid for eid in (_equation_id(row) for row in equations) if eid],
        "symbol_ids": [sid for sid in (_symbol_id(row) for row in symbols) if sid],
        "reconstructed_inputs": reconstructed,
        "facts": [],
    }
    empty_contract = {"inputs": [], "outputs": [], "parameters": []}
    if not any(_has_text(row) for row in equations):
        provenance["reconstructed_inputs"] = False
        return schema.ProgramDraft(
            source_code="",
            io_contract=empty_contract,
            provenance=provenance,
            facts=[schema.FACT_NO_MODULE_EQUATIONS],
            ast_check=None,
        )

    prompt = build_codegen_prompt(module, equations=equations, symbols=symbols)
    call_kwargs: dict[str, Any] = {
        "structured_fn": structured_fn,
        "text_fn": text_fn,
        "text_fallback": bool(text_fn),
        "model": model,
        "repair_backslashes": True,
        "degraded": None,
        "log_label": "simulation codegen",
    }
    if reasoning_effort is not None:
        call_kwargs["reasoning_effort"] = reasoning_effort

    system_facts: list[str] = []
    if reconstructed:
        system_facts.append(schema.FACT_RECONSTRUCTED_INPUTS)

    data = _as_dict(structured_call(prompt, _ProgramResponse, **call_kwargs))
    if data is None:
        provenance["facts"] = []
        return schema.ProgramDraft(
            source_code="",
            io_contract=empty_contract,
            provenance=provenance,
            facts=_dedupe([FACT_GENERATION_FAILED, *system_facts]),
            ast_check=None,
        )
    source = _source_of(data)
    check = ast_guard.check_program(source)
    if not (check.ok and check.entrypoint_found):
        messages = [
            {"role": "user", "content": prompt},
            {"role": "user", "content": _repair_prompt(check.reasons, source)},
        ]
        repaired = _as_dict(structured_call(messages, _ProgramResponse, **call_kwargs))
        if repaired is not None:
            data = repaired
            source = _source_of(data)
            check = ast_guard.check_program(source)

    contract = _normalize_contract(data.get("io_contract"))
    contract = complete_contract_from_source(contract, source)
    llm_facts = _normalize_facts(data.get("facts"))
    provenance["facts"] = llm_facts
    if not (check.ok and check.entrypoint_found):
        system_facts.append(schema.FACT_AST_REJECTED)
    expected = _output_symbols(module, equations)
    declared = {item["symbol"] for item in contract["outputs"]}
    if expected and declared and not symbols_overlap(declared, expected):
        system_facts.append(schema.outputs_not_matching_fact(declared, expected))
    return schema.ProgramDraft(
        source_code=source,
        io_contract=contract,
        provenance=provenance,
        facts=_dedupe([*system_facts, *llm_facts]),
        ast_check=check,
    )


def program_facts_for_display(draft: schema.ProgramDraft) -> list[str]:
    """教員向け表示用の事実文（重複除去・制御文字除去・数値スコアなし）。"""
    facts = list(getattr(draft, "facts", None) or [])
    check = getattr(draft, "ast_check", None)
    if check is not None and not (check.ok and check.entrypoint_found):
        facts.append(schema.FACT_AST_REJECTED)
        facts.extend(check.reasons)
    provenance = getattr(draft, "provenance", None) or {}
    if provenance.get("reconstructed_inputs"):
        facts.append(schema.FACT_RECONSTRUCTED_INPUTS)
    return _dedupe([strip_control_sequences(str(f)).strip() for f in facts])


def complete_contract_from_source(contract: dict[str, list[dict]], source: str) -> dict[str, list[dict]]:
    """コードが読むキーで io_contract を補完する（決定論・SB3 の AST と同じ走査）。

    LLM が ``params["b_1"]`` を使いながら契約に ``b_1`` を書かないと、入力欄に現れず実行が
    ``KeyError`` で落ちる（実機 2026-10-03）。契約に無いキーは ``kind`` を inputs=``variable`` /
    params=``parameter``、``default`` は None（画面では空欄・実行前検査で名指しされる）として足す。
    既存の項目は変えない。
    """
    out = {k: [dict(i) for i in (contract.get(k) or [])] for k in ("inputs", "outputs", "parameters")}
    keys = ast_guard.required_keys(source)
    have_inputs = {str(i.get("symbol") or "") for i in out["inputs"]}
    have_params = {str(i.get("symbol") or "") for i in out["parameters"]}
    for name in sorted(keys["inputs"]):
        if name and name not in have_inputs:
            out["inputs"].append({"symbol": name, "kind": "variable", "unit": "", "default": None})
    for name in sorted(keys["params"]):
        if name and name not in have_params and name not in have_inputs:
            out["parameters"].append({"symbol": name, "kind": "parameter", "unit": "", "default": None})
    return out

