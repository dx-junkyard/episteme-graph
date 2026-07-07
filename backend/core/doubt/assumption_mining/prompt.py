"""暗黙前提の正規化プロンプト（D2-2）。

claim_qualification の原子化と同じ型: 1 前提 = 1 minimal proposition。
LLM は前提文の言語化のみを行い、「これは暗黙の前提である」という**確定**は
行わない（確定は教員, D2-4）。
"""

from __future__ import annotations

from core.doubt.assumption_mining.schema import MAX_STATEMENT_CHARS

_INSTRUCTION = """あなたは理論物理・数理科学文献の「暗黙の前提」候補を言語化するアシスタントです。

# 課題
複数の論文・導出にまたがって反復している「AIが行間を埋めるために補った補完ステップ」の
クラスタ情報が与えられます。この補完が暗黙のうちに依存している前提を、
**原子化された前提文 1 文**（1 前提 = 1 命題）に正規化してください。

# 絶対のルール
1. 出力はあくまで**候補**。断定形の「〜は正しい」ではなく、前提の内容を中立に記述する
   （例: 「摂動展開は高次項を無視しても収束する」）。
2. statement は {max_chars} 文字以内の 1 文。複数の命題を含めない。
3. evidence_quote は与えられたクラスタ情報テキストからの**逐語引用**にする。
4. reason には「なぜこの補完がこの前提に依存すると言えるか」を短く書く。
5. confidence は 0.0〜1.0。手掛かりが間接的なら低くする。
6. 与えられた情報から前提を言語化できない場合は statement を空文字列にする
   （無理に作らない）。

# 出力JSON（この形式のみ）
{{
  "statement": "",
  "evidence_quote": "",
  "reason": "",
  "confidence": 0.0
}}
"""


def build_instruction() -> str:
    return _INSTRUCTION.format(max_chars=MAX_STATEMENT_CHARS)


def build_content(operation: str, review_reasons: list[str], source_texts: list[str]) -> str:
    lines = [build_instruction(), "", "# クラスタ情報"]
    lines.append(f"- 補完ステップの operation: {operation or 'unknown_operation'}")
    if review_reasons:
        lines.append(f"- review 理由: {', '.join(sorted(set(review_reasons)))}")
    lines.append("")
    lines.append("# クラスタ情報テキスト（evidence_quote はここからの逐語引用）")
    for idx, text in enumerate(source_texts, start=1):
        cleaned = (text or "").strip()
        if cleaned:
            lines.append(f"[T{idx}] {cleaned}")
    return "\n".join(lines)


def build_repair_prompt(previous_raw: str, errors: list[str]) -> str:
    error_lines = "\n".join(f"- {e}" for e in errors)
    return (
        "# 修復指示\n"
        "直前のあなたの出力は以下のルール違反がありました:\n"
        f"{error_lines}\n\n"
        "違反箇所だけを修正し、同じ JSON 形式で全体を出力し直してください。\n\n"
        f"直前の出力:\n{previous_raw}"
    )
