"""SB層 codegen（§5.1）— LLM は fake 注入。1 コール + 修復 1 回・untrusted 区画・出所の正直さ。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.simulation_bridge import codegen, schema  # noqa: E402
from core.text_hygiene import UNTRUSTED_SOURCE_NOTICE  # noqa: E402

GOOD_CODE = "import numpy as np\n\ndef simulate(inputs, params):\n    return {'y': inputs['x'] * params.get('a', 1.0)}\n"
BAD_CODE = "import os\n\ndef simulate(inputs, params):\n    return {'y': os.getcwd()}\n"

MODULE = {
    "module_key": "m1",
    "label": "線形化 → 消去",
    "theory_object": "y",
    "inputs": [{"equation_id": "eq_1", "display_label": "式 (1)"}],
    "outputs": [{"equation_id": "eq_2", "display_label": "式 (2)"}],
    "foundation": [],
    "assumptions": [{"text": "小振幅を仮定する", "origin_label": "本文"}],
    "members": [{"step_refs": ["s1"], "operation_label": "線形化", "input_labels": ["式 (1)"], "output_labels": ["式 (2)"]}],
}
EQUATIONS = [
    {"agent_equation_id": "eq_1", "label": "(1)", "latex": "x = a t", "plain_text": "", "defined_symbols": ["x"], "used_symbols": ["a"], "agent_payload": {}},
    {"agent_equation_id": "eq_2", "label": "(2)", "latex": "y = a x", "plain_text": "", "defined_symbols": ["y"], "used_symbols": ["x"], "agent_payload": {}},
]
SYMBOLS = [{"canonical_symbol": "a", "kind": "parameter", "unit": "m", "scope": "global", "definition_evidence_texts": ["a は係数"], "agent_payload": {"domain_constraints": ["a > 0"]}}]


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, *, messages, response_format, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return response_format(**item)


def _resp(code, outputs=("y",)):
    return {
        "source_code": code,
        "io_contract": {"inputs": [{"symbol": "x", "kind": "variable", "unit": "", "default": 1.0}],
                        "outputs": [{"symbol": s} for s in outputs],
                        "parameters": [{"symbol": "a", "kind": "parameter", "unit": "m", "default": "1", "confidence": 0.9}]},
        "facts": ["高次項を省いた"],
    }


def test_good_code_is_accepted_in_one_call():
    llm = FakeLLM(_resp(GOOD_CODE))
    draft = codegen.generate_program(MODULE, equations=EQUATIONS, symbols=SYMBOLS, structured_fn=llm, model="m-x")
    assert draft.accepted
    assert len(llm.calls) == 1
    assert llm.calls[0]["kwargs"].get("model") == "m-x"
    assert draft.io_contract["parameters"][0] == {"symbol": "a", "kind": "parameter", "unit": "m", "default": "1"}
    assert set(draft.io_contract) == {"inputs", "outputs", "parameters"}
    assert draft.provenance["equation_ids"] == ["eq_1", "eq_2"]
    assert draft.provenance["reconstructed_inputs"] is False
    assert draft.provenance["model"] == "m-x"
    assert "高次項を省いた" in draft.facts
    assert "confidence" not in json.dumps(draft.io_contract) + json.dumps(draft.provenance)


def test_bad_then_good_uses_exactly_one_repair():
    llm = FakeLLM(_resp(BAD_CODE), _resp(GOOD_CODE))
    draft = codegen.generate_program(MODULE, equations=EQUATIONS, symbols=SYMBOLS, structured_fn=llm)
    assert draft.accepted
    assert len(llm.calls) == 2
    repair = llm.calls[1]["messages"][-1]["content"]
    assert "os" in repair and "import os" in repair


def test_bad_twice_is_rejected_with_fact():
    llm = FakeLLM(_resp(BAD_CODE), _resp(BAD_CODE))
    draft = codegen.generate_program(MODULE, equations=EQUATIONS, symbols=SYMBOLS, structured_fn=llm)
    assert not draft.accepted
    assert len(llm.calls) == 2
    assert schema.FACT_AST_REJECTED in draft.facts
    assert draft.ast_check is not None and not draft.ast_check.ok


def test_no_equations_text_no_llm_call():
    llm = FakeLLM()
    eqs = [{"agent_equation_id": "eq_1", "latex": "", "plain_text": "  "}]
    draft = codegen.generate_program(MODULE, equations=eqs, symbols=[], structured_fn=llm)
    assert llm.calls == []
    assert not draft.accepted
    assert draft.facts == [schema.FACT_NO_MODULE_EQUATIONS]


def test_reconstructed_flag_propagates():
    eqs = [dict(EQUATIONS[0], agent_payload={"confidence_policy": {"must_not_treat_as_source_extracted": True}}), EQUATIONS[1]]
    llm = FakeLLM(_resp(GOOD_CODE))
    draft = codegen.generate_program(MODULE, equations=eqs, symbols=SYMBOLS, structured_fn=llm)
    assert draft.provenance["reconstructed_inputs"] is True
    assert schema.FACT_RECONSTRUCTED_INPUTS in draft.facts
    assert schema.FACT_RECONSTRUCTED_INPUTS in codegen.program_facts_for_display(draft)
    prompt = llm.calls[0]["messages"][0]["content"]
    assert '"reconstructed": true' in prompt


def test_prompt_puts_material_inside_untrusted_json_block():
    llm = FakeLLM(_resp(GOOD_CODE))
    codegen.generate_program(MODULE, equations=EQUATIONS, symbols=SYMBOLS, structured_fn=llm)
    prompt = llm.calls[0]["messages"][0]["content"]
    assert UNTRUSTED_SOURCE_NOTICE in prompt
    notice_at = prompt.index(UNTRUSTED_SOURCE_NOTICE)
    json_start = prompt.index("```json", notice_at) + len("```json")
    json_end = prompt.index("```", json_start)
    material = json.loads(prompt[json_start:json_end])
    assert [e["latex"] for e in material["equations"]] == ["x = a t", "y = a x"]
    assert [e["role"] for e in material["equations"]] == ["input", "output"]
    assert "x = a t" not in prompt[:notice_at]
    assert material["symbols"][0]["domain_constraints"] == ["a > 0"]


def test_output_symbol_mismatch_is_a_fact_not_a_rejection():
    llm = FakeLLM(_resp(GOOD_CODE, outputs=("z",)))
    draft = codegen.generate_program(MODULE, equations=EQUATIONS, symbols=SYMBOLS, structured_fn=llm)
    assert draft.accepted
    fact = schema.outputs_not_matching_fact({"z"}, {"y"})
    assert fact in draft.facts
    assert "（z）" in fact and "（y）" in fact


def test_llm_failure_degrades_to_fact():
    llm = FakeLLM(RuntimeError("down"))
    draft = codegen.generate_program(MODULE, equations=EQUATIONS, symbols=SYMBOLS, structured_fn=llm)
    assert not draft.accepted
    assert codegen.FACT_GENERATION_FAILED in draft.facts


def test_text_fallback_used_when_given():
    calls = []

    def text_fn(*, messages, **kwargs):
        calls.append(messages)
        return "```json\n" + json.dumps(_resp(GOOD_CODE)) + "\n```"

    draft = codegen.generate_program(
        MODULE, equations=EQUATIONS, symbols=SYMBOLS, structured_fn=FakeLLM(RuntimeError("no structured")), text_fn=text_fn
    )
    assert draft.accepted
    assert len(calls) == 1


def test_code_fence_in_source_is_stripped():
    llm = FakeLLM(_resp("```python\n" + GOOD_CODE + "```"))
    draft = codegen.generate_program(MODULE, equations=EQUATIONS, symbols=SYMBOLS, structured_fn=llm)
    assert draft.accepted
    assert not draft.source_code.startswith("```")


# ---------------------------------------------------------------------------
# 第 21 周（R8 内部語の衛生・R9 名指し）
# ---------------------------------------------------------------------------


def test_output_symbols_matching_has_no_mismatch_fact():
    llm = FakeLLM(_resp(GOOD_CODE, outputs=("y",)))
    draft = codegen.generate_program(MODULE, equations=EQUATIONS, symbols=SYMBOLS, structured_fn=llm)
    assert not any("重なっていません" in f for f in draft.facts)


def test_role_words_are_humanized_in_facts():
    out = codegen._normalize_facts([
        "y（role=output）は x から計算した。",
        "role=member の式は省いた。",
        "role=foundation を定数とした。",
        "role=input を受ける。",
    ])
    assert out == [
        "y（出力の式）は x から計算した。",
        "手順の中の式 の式は省いた。",
        "共有の基礎の式 を定数とした。",
        "入力の式 を受ける。",
    ]
    assert not any("role" in f for f in out)


def test_unknown_role_fact_is_dropped():
    out = codegen._normalize_facts(["role=sink は無視した。", "近似として線形にした。"])
    assert out == ["近似として線形にした。"]


def test_prompt_tells_facts_are_for_learners():
    prompt = codegen.build_codegen_prompt(MODULE, equations=EQUATIONS, symbols=SYMBOLS)
    notice_at = prompt.index(UNTRUSTED_SOURCE_NOTICE)
    assert "role= などの内部の語・英語の型名を書かないでください" in prompt[:notice_at]


# ---------------------------------------------------------------------------
# 第 22 周 A3（IK-0627）: 資料の整形・記号表の絞り・計算できないときの形
# ---------------------------------------------------------------------------


def _material(module, equations, symbols):
    llm = FakeLLM(_resp(GOOD_CODE))
    codegen.generate_program(module, equations=equations, symbols=symbols, structured_fn=llm)
    prompt = llm.calls[0]["messages"][0]["content"]
    json_start = prompt.index("```json") + len("```json")
    json_end = prompt.index("```", json_start)
    return prompt, json.loads(prompt[json_start:json_end])


DICT_EQUATIONS = [
    {
        "agent_equation_id": "eq_1", "label": "", "latex": "S = \\int L \\, dx", "plain_text": "S = \\int L \\, dx",
        "defined_symbols": [
            {"symbol": "S", "meaning": "The theory action. It is integrated.", "symbol_id": "sym_abc_S", "definition_status": "defined"},
            {"symbol": "L", "meaning": None, "symbol_id": "sym_abc_L", "definition_status": "used"},
        ],
        "used_symbols": ["x"],
        "agent_payload": {"semantics": {"equation_type": "definition", "summary": "Defines the full action as the integral of L."}},
    },
    {"agent_equation_id": "eq_2", "label": "(2)", "latex": "y = a x", "plain_text": "", "defined_symbols": ["y"],
     "used_symbols": ["x"], "agent_payload": {}},
]


def test_dict_symbols_become_names_not_repr():
    prompt, material = _material(MODULE, DICT_EQUATIONS, SYMBOLS)
    first = material["equations"][0]
    assert first["defined_symbols"] == ["S"]
    assert "L" in first["used_symbols"] and "x" in first["used_symbols"]
    assert "{'symbol'" not in prompt and "sym_abc" not in prompt and "symbol_id" not in prompt
    assert "definition_status" not in prompt


def test_english_summary_is_not_used_as_label():
    _, material = _material(MODULE, DICT_EQUATIONS, SYMBOLS)
    first = material["equations"][0]
    assert "Defines" not in first["label"]
    assert first["label"].startswith("番号なし")
    assert first["summary"].startswith("Defines the full action")
    assert material["equations"][1]["label"] == "式 (2)"
    # plain_text が latex と同じなら重ねて載せない
    assert "plain_text" not in first


def test_member_prose_labels_are_folded():
    module = dict(MODULE, members=[{"operation_label": "定義", "input_labels": ["番号なし: Defines the full Horndeski action as the"],
                                    "output_labels": ["式 (2)"]}])
    _, material = _material(module, EQUATIONS, SYMBOLS)
    member = material["module"]["members"][0]
    assert member["inputs"] == ["番号なし"]
    assert member["outputs"] == ["式 (2)"]


def test_symbol_table_is_limited_to_this_module_and_drops_unknowns():
    symbols = [
        {"canonical_symbol": "a", "kind": "unknown", "unit": "", "scope": "document", "definition_evidence_texts": ["a は係数です。続きの文。"], "agent_payload": {}},
        {"canonical_symbol": "M_PISN", "kind": "unknown", "unit": "", "scope": "document", "definition_evidence_texts": ["遠い記号"], "agent_payload": {}},
        {"canonical_symbol": "x", "kind": "", "unit": "", "scope": "section", "definition_evidence_texts": [], "agent_payload": {}},
    ]
    _, material = _material(MODULE, EQUATIONS, symbols)
    names = [s["symbol"] for s in material["symbols"]]
    assert names == ["a"]  # 式に無い M_PISN は落ちる・名前だけの x も載せない
    item = material["symbols"][0]
    assert item == {"symbol": "a", "meaning": "a は係数です。続きの文。"} or "kind" not in item
    assert "unit" not in item and "kind" not in item and "scope" not in item


def test_symbol_meaning_falls_back_to_equation_dict_meaning():
    symbols = [{"canonical_symbol": "S", "kind": "unknown", "unit": "", "definition_evidence_texts": [], "agent_payload": {}}]
    _, material = _material(MODULE, DICT_EQUATIONS, symbols)
    assert material["symbols"] == [{"symbol": "S", "meaning": "The theory action."}]


def test_symbol_table_upper_bound():
    many = [{"canonical_symbol": f"s{i}", "unit": "m", "definition_evidence_texts": [], "agent_payload": {}} for i in range(80)]
    eqs = [dict(EQUATIONS[0], used_symbols=[f"s{i}" for i in range(80)])]
    _, material = _material(MODULE, eqs, many)
    assert len(material["symbols"]) == codegen.SYMBOL_TABLE_LIMIT


def test_output_note_only_when_module_has_no_output_equation():
    _, with_output = _material(MODULE, EQUATIONS, SYMBOLS)
    assert "output_note" not in with_output
    module = dict(MODULE, outputs=[])
    _, without = _material(module, EQUATIONS, SYMBOLS)
    assert without["output_note"] == codegen.MATERIAL_NO_OUTPUT_NOTE


def test_instruction_describes_cannot_compute():
    prompt, _ = _material(MODULE, EQUATIONS, SYMBOLS)
    head = prompt[: prompt.index(UNTRUSTED_SOURCE_NOTICE)]
    assert "cannot_compute" in head and "部分実装で埋めない" in head


def test_cannot_compute_is_normalized_and_named_in_facts():
    resp = _resp(GOOD_CODE, outputs=())
    resp["cannot_compute"] = [
        {"symbol": "\\xi_\\pm", "reason": "核が外部定義で role=output の値を出せない"},
        {"symbol": ""},
        "P_m",
        {"symbol": "\\xi_\\pm", "reason": "重複"},
    ]
    llm = FakeLLM(resp)
    draft = codegen.generate_program(MODULE, equations=EQUATIONS, symbols=SYMBOLS, structured_fn=llm)
    blocked = draft.io_contract["cannot_compute"]
    assert [b["symbol"] for b in blocked] == ["\\xi_\\pm", "P_m"]
    assert "role=" not in blocked[0]["reason"] and "出力の式" in blocked[0]["reason"]
    fact = schema.cannot_compute_fact(blocked)
    assert fact in draft.facts and fact in codegen.program_facts_for_display(draft)
    assert "\\xi_\\pm" in fact and "P_m" in fact
    assert not any(ch.isdigit() for ch in schema.FACT_CANNOT_COMPUTE)
    # 出力が空なら出力記号の食い違い警告は出さない
    assert not any("出力の式" in f and "記号" in f and "一致" in f for f in draft.facts if f != fact)


def test_cannot_compute_inside_contract_is_accepted_and_kept_through_completion():
    resp = _resp(GOOD_CODE, outputs=())
    resp["io_contract"]["cannot_compute"] = [{"symbol": "C_l", "reason": "関数形が無い"}]
    draft = codegen.generate_program(MODULE, equations=EQUATIONS, symbols=SYMBOLS, structured_fn=FakeLLM(resp))
    assert draft.io_contract["cannot_compute"] == [{"symbol": "C_l", "reason": "関数形が無い"}]
    assert set(draft.io_contract) == {"inputs", "outputs", "parameters", "cannot_compute"}


def test_no_cannot_compute_key_when_nothing_blocked():
    draft = codegen.generate_program(MODULE, equations=EQUATIONS, symbols=SYMBOLS, structured_fn=FakeLLM(_resp(GOOD_CODE)))
    assert "cannot_compute" not in draft.io_contract
