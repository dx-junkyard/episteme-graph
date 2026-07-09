"""検証スコープ候補抽出のプロンプト定義（D1-4）。

重要な姿勢: この LLM は「検証されているか」を**判定しない**。出典テキストに
明示されている適用条件・領域・精度・系の記述を、検証スコープの**候補**として
書き写すだけ。確定（記帳）は教員が行う。
"""

from __future__ import annotations

from core.doubt.scope_candidates.schema import (
    MAX_CANDIDATES_PER_TARGET,
    ScopeTargetContext,
)

_INSTRUCTION = """あなたは学術文献の「検証スコープ」候補を抽出するアシスタントです。

# 課題
対象（claim / equation / component / assumption）について、出典テキストに
**明示されている**適用条件を「検証スコープ候補」として抽出してください。
検証スコープとは「この命題・式がどの条件・領域・精度・系で成り立つ（検証された）と
述べられているか」の記帳単位です。

# 絶対のルール
1. 出典テキストに書かれていることだけを使う。一般知識で補完しない。
2. 「検証されている」と断定しない。あなたの出力は候補であり、確定は人間（教員）が行う。
3. evidence_quote は出典テキストからの**逐語引用**（一字一句そのまま）にする。
4. condition / domain / precision / system のうち、出典から読み取れた軸だけを埋める
   （少なくとも 1 つ。読み取れない軸は空文字列のまま）。
5. reason には「出典のどの記述からそう言えるか」を短く書く。
6. confidence は 0.0〜1.0。出典の記述が間接的・曖昧なら低くする。
7. 候補が出典から読み取れない場合は candidates を空配列にする（無理に作らない）。
8. 候補は最大 {max_candidates} 件。

# 4 軸の意味
- condition: 成立条件（例: 低エネルギー極限で / 弱結合の範囲で）
- domain: 領域・相互作用の種類（例: 電磁相互作用で / 強い相互作用で）
- precision: 精度・次数（例: 一次摂動の精度で / ツリーレベルで）
- system: 検証された系・対象（例: 水素原子で / β崩壊で）

# 出力JSON（この形式のみ。他のテキストを出力しない）
{{
  "candidates": [
    {{
      "condition": "",
      "domain": "",
      "precision": "",
      "system": "",
      "evidence_quote": "出典からの逐語引用",
      "reason": "",
      "confidence": 0.0
    }}
  ]
}}
"""


def build_instruction() -> str:
    return _INSTRUCTION.format(max_candidates=MAX_CANDIDATES_PER_TARGET)


def build_content(context: ScopeTargetContext) -> str:
    """instruction + 対象・出典ブロックを user ロール 1 本に連結する。"""
    lines = [build_instruction(), "", "# 対象"]
    lines.append(f"- target_type: {context.target_type}")
    lines.append(f"- target_id: {context.target_id}")
    if context.target_label:
        lines.append(f"- label: {context.target_label}")
    lines.append("")
    lines.append("# 出典テキスト")
    for block in context.source_blocks:
        text = (block.text or "").strip()
        if not text:
            continue
        lines.append(f"[{block.block_id}] {block.label}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def build_repair_prompt(previous_raw: str, errors: list[str]) -> str:
    """validation 失敗時の修復指示。"""
    error_lines = "\n".join(f"- {e}" for e in errors)
    return (
        "# 修復指示\n"
        "直前のあなたの出力は以下のルール違反がありました:\n"
        f"{error_lines}\n\n"
        "違反箇所だけを修正し、同じ JSON 形式で全体を出力し直してください。\n"
        "evidence_quote は必ず出典テキストの逐語引用にしてください。\n\n"
        f"直前の出力:\n{previous_raw}"
    )
