"""反証条件候補抽出のプロンプト定義（SL-1）。

重要な姿勢: この LLM は「検証されているか」も「反証可能かどうか」も**判定しない**。
出典テキストに書かれている内容から、「何が観測・測定されたらこの対象が覆るか」を
候補として書き写すだけ。確定（記帳）は教員が行う。反証不可能という判定・到達可能性の
評価は人間専用であり、この LLM には一切行わせない。
"""

from __future__ import annotations

from core.doubt.falsification_conditions.schema import (
    MAX_CANDIDATES_PER_TARGET,
    FalsificationTargetContext,
)

_INSTRUCTION = """あなたは学術文献から「反証条件」候補を抽出するアシスタントです。

# 課題
対象（claim / equation / component / assumption）について、「何が観測・測定されたら
この対象が覆る（誤りだと分かる）か」を**反証条件候補**として書いてください。
反証条件とは「この主張が偽であることを示す観測・測定の言明」です。

# 絶対のルール
1. 出典テキストに書かれていることだけを使う。一般知識で補完しない。
2. あなたは「検証されている」「反証可能である」「反証不可能である」のいずれも判定しない。
   あなたの出力は候補であり、確定は人間（教員）が行う。
3. 反証条件は必ず「観測・測定の言明」の形で書く（例: 「〇〇の測定値が××の範囲外になれば」）。
   評価・感想・分野全体についての一般論は書かない。
4. kind は次の2値のうち必ず一方を選ぶ: "observation_value"（観測値そのものが想定と
   異なる場合）/ "auxiliary_hypothesis"（較正・装置・前提モデルなどの補助仮説が
   誤っている場合）。この2値以外の値・第3の区分は出力しない。
5. **到達可能性（現在の観測で確かめられるか、将来の装置が必要か等）は一切評価しない。**
   その判断は人間専用であり、あなたの出力フィールドにも含めない。
6. **この対象・この主張について、分野全体・研究コミュニティ全体を主語にした
   断定を書かない。** あなたが見ているのは今回渡された出典テキストの範囲だけであり、
   それ以外の文献・研究者の状況を一切知らない。書けるのはこの出典テキストが
   何を述べているか・述べていないかだけである。
7. evidence_quote は出典テキストからの**逐語引用**（一字一句そのまま）にする。
8. reason には「出典のどの記述からそう言えるか」を短く書く。
9. confidence は 0.0〜1.0。出典の記述が間接的・曖昧なら低くする。
10. 候補が出典から読み取れない場合は candidates を空配列にする（無理に作らない）。
11. 候補は最大 {max_candidates} 件。

# 出力JSON（この形式のみ。他のテキストを出力しない）
{{
  "candidates": [
    {{
      "statement": "何が観測・測定されたら覆るかの言明",
      "kind": "observation_value",
      "evidence_quote": "出典からの逐語引用",
      "reason": "",
      "confidence": 0.0
    }}
  ]
}}
"""


def build_instruction() -> str:
    return _INSTRUCTION.format(max_candidates=MAX_CANDIDATES_PER_TARGET)


def build_content(context: FalsificationTargetContext) -> str:
    """instruction + 対象・出典ブロック + 下流要約を user ロール1本に連結する。"""
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
    if context.downstream_labels:
        lines.append(
            "# 参考: この対象に依存している既知の要素（覆えたときの帰結の具体性に"
            "使ってよい。判定の根拠にはしない）"
        )
        for label in context.downstream_labels:
            lines.append(f"- {label}")
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
        "evidence_quote は必ず出典テキストの逐語引用にしてください。\n"
        "kind は observation_value / auxiliary_hypothesis のいずれかにしてください。\n\n"
        f"直前の出力:\n{previous_raw}"
    )
