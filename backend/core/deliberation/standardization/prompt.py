"""標準化判定（Phase S）— LLM 事前知識（証拠①）のプロンプト定義。

重要な姿勢: この LLM は「標準である」と断定しない。あなたの一般知識に基づく申告
（三角測量の一証拠）に過ぎず、他の2証拠（L層凍結版類似・コーパス内反復）との
突き合わせ（``aggregate.py``・非LLM）を経て、最終確定は常に人間（教員）が行う（KN-3）。

出典テキストの逐語引用検証は行わない（この LLM 呼び出しは特定の文書の書き写しではなく、
一般知識の申告のため）。その代わり ``reason`` を必須にし、無根拠の断定を弾く
（``validator.py``）。
"""

from __future__ import annotations

from core.deliberation.standardization.input_builder import StandardizationTargetContext

_INSTRUCTION = """あなたは学術分野の「標準化判定」を補助するアシスタントです。

# 課題
以下に提示する「共通部品」（複数の論文にまたがって再利用されうる概念・装置・パーツの
候補）について、あなたの一般的な知識に基づいて判定してください。

# 絶対のルール
1. 確信が持てない場合は正直に recognized=false / breadth="unknown" とする。
   もっともらしい正式名称を捏造しない。分野・分派によって呼び方が異なり、
   あなたが単に知らないだけの可能性もあるため、確信が無ければ弱く判定すること。
2. あなたの判定はあくまで候補（三角測量の一証拠）であり、最終確定は人間が
   他の証拠（分野内での既存整理・複数論文での反復）と突き合わせて行う。

# 判定項目
- recognized (bool): 学術分野で広く知られた標準的な概念・装置・パーツか
- claimed_canonical_name (string): 広く使われる正式名称・呼称（無ければ空文字）
- breadth ("textbook" | "field" | "unknown"): 認知の広さ。
  "textbook" = 教科書レベルで確立、"field" = 特定分野内では知られているが
  教科書レベルとまでは言えない、"unknown" = 判断できない、または recognized=false
- reason (string): そう判断した理由（簡潔に）
- confidence (number 0.0〜1.0)

# 出力JSON（この形式のみ。他のテキストを出力しない）
{
  "recognized": false,
  "claimed_canonical_name": "",
  "breadth": "unknown",
  "reason": "",
  "confidence": 0.0
}
"""


def build_instruction() -> str:
    return _INSTRUCTION


def build_content(context: StandardizationTargetContext) -> str:
    """instruction + 対象（共通部品の名称・別名・要約・本文）を user ロール1本に連結する。"""
    lines = [build_instruction(), "", "# 対象（共通部品）"]
    lines.append(f"- domain: {context.domain_key}")
    lines.append(f"- entry_type: {context.entry_type}")
    lines.append(f"- name: {context.name}")
    if context.aliases:
        lines.append(f"- aliases: {', '.join(context.aliases)}")
    if context.summary:
        lines.append(f"- summary: {context.summary}")
    if context.body:
        lines.append(f"- body: {context.body}")
    return "\n".join(lines)


def build_repair_prompt(previous_raw: str, errors: list[str]) -> str:
    """validation 失敗時の修復指示。"""
    error_lines = "\n".join(f"- {e}" for e in errors)
    return (
        "# 修復指示\n"
        "直前のあなたの出力は以下のルール違反がありました:\n"
        f"{error_lines}\n\n"
        "違反箇所だけを修正し、同じ JSON 形式で全体を出力し直してください。\n\n"
        f"直前の出力:\n{previous_raw}"
    )
