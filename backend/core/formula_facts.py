"""学習チャットの文脈に添える「式の事実」1行（IK-0577・決定論・非LLM・FastAPI 非 import）。

出典ポップアップと教材表示は、式ごとに①論文に印字された式番号（「式 (12)」）と
②AI が文脈から復元した式の印（``label_vocab.RECONSTRUCTED_EQUATION_MARK``）を描く
（``core/lecture.annotate_reconstructed_formulas``）。チャットの文脈にはこの事実が
届いていなかったため、チャットが復元式を論文の式として断定したり「印は無い」と
言ったりした。ここでは画面が描くのと同じ事実だけを、チャンク・トピック1つにつき
1行にまとめる（件数・類似度などの数値は書かない。書く数字は式番号だけ）。
"""

from __future__ import annotations

import json
from typing import Any

from core.label_vocab import RECONSTRUCTED_EQUATION_MARK, RECONSTRUCTED_EQUATION_NOTE
from core.lecture import annotate_reconstructed_formulas

#: 文脈に置く事実行の見出し（チャットのプロンプトと system の指示が同じ語で指す）。
FORMULA_FACT_HEADING = "この箇所の式"
#: 印字番号の無い復元式の呼び方（数えない）。
UNNUMBERED_FORMULA_WORD = "番号のない式"

#: 事実行に復元式が含まれるときだけ system に足す1文（復元式を原文として断定させない）。
RECONSTRUCTED_FORMULA_INSTRUCTION = (
    f"【式の出所】文脈の「{FORMULA_FACT_HEADING}」に［{RECONSTRUCTED_EQUATION_MARK}］と"
    "付いた式は、AI が文脈から復元した式で、原文の数式とは照合できていません。"
    "復元式は原文の式として断定しないでください（式に触れるときは、復元された式であることを添えてください）。"
)


def _as_formula_list(formulas: Any) -> list[dict]:
    items = formulas
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:  # noqa: BLE001
            return []
    if not isinstance(items, list):
        return []
    return [f for f in items if isinstance(f, dict)]


def _printed_label(formula: dict) -> str:
    from core.course_content_builder import equation_printed_label

    return equation_printed_label(formula)


def formula_fact_line(formulas: Any, *, referenced_ids: Any = None) -> str:
    """式の事実を1行にする。事実が無ければ ``""``。

    - 印字番号のある式は「式 (N)」、復元式には ``［AI復元］`` を添える。
    - 印字番号の無い復元式は「番号のない式［AI復元］」を1度だけ書く（数えない）。
    - 印字番号も復元の印も無い式は書かない（言える事実が無い）。
    - ``referenced_ids`` を渡すと、その id の式だけを対象にする（本文が参照した式）。
    - 復元式があるときだけ末尾に ``RECONSTRUCTED_EQUATION_NOTE`` を添える。
    """
    items = annotate_reconstructed_formulas(_as_formula_list(formulas))
    allowed = None
    if referenced_ids is not None:
        allowed = {str(x).strip() for x in referenced_ids if str(x or "").strip()}
    parts: list[str] = []
    has_reconstructed = False
    unnumbered_reconstructed = False
    for formula in items:
        if allowed is not None:
            fid = str(formula.get("id") or formula.get("equation_id") or "").strip()
            if fid not in allowed:
                continue
        printed = _printed_label(formula)
        reconstructed = bool(formula.get("reconstructed_mark"))
        has_reconstructed = has_reconstructed or reconstructed
        mark = f"［{RECONSTRUCTED_EQUATION_MARK}］" if reconstructed else ""
        if printed:
            part = printed + mark
            if part not in parts:
                parts.append(part)
        elif reconstructed:
            unnumbered_reconstructed = True
    if unnumbered_reconstructed:
        parts.append(UNNUMBERED_FORMULA_WORD + f"［{RECONSTRUCTED_EQUATION_MARK}］")
    if not parts:
        return ""
    line = f"（{FORMULA_FACT_HEADING}: " + "、".join(parts)
    if has_reconstructed:
        line += f"。［{RECONSTRUCTED_EQUATION_MARK}］= {RECONSTRUCTED_EQUATION_NOTE}"
    return line + "）"


def has_reconstructed_fact(line: str) -> bool:
    """事実行に復元式が含まれるか。"""
    return f"［{RECONSTRUCTED_EQUATION_MARK}］" in (line or "")
