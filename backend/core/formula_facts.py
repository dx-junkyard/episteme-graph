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
#: 印字番号も本文の手がかりも無い復元式の呼び方（数えない）。
UNNUMBERED_FORMULA_WORD = "番号のない式"
#: 番号の無い復元式を式本文の先頭で名指すときの字数（数値は書かない — 字数は内部の上限）。
UNNUMBERED_SNIPPET_MAX = 24
#: 同じ箇所に印の無い式もあるときに添える事実（印の無い式は原文の式であることを言う）。
ORIGINAL_FORMULAS_NOTE = "印の無い式は原文の数式です"

#: 事実行に復元式が含まれるときだけ system に足す1文（復元式を原文として断定させない）。
#: 第 25 周 第 5 波（IK-0997 / IK-1009）: 印の説明（［AI復元］= …）は事実行ごとに繰り返さず、
#: ここに 1 回だけ置く。印は式ごとに付き、印の無い式は原文の式として扱ってよい。
RECONSTRUCTED_FORMULA_INSTRUCTION = (
    f"【式の出所】文脈の「{FORMULA_FACT_HEADING}」に［{RECONSTRUCTED_EQUATION_MARK}］と"
    f"付いた式（［{RECONSTRUCTED_EQUATION_MARK}］= {RECONSTRUCTED_EQUATION_NOTE}）は、"
    "AI が文脈から復元した式で、原文の数式とは照合できていません。"
    "復元式は原文の式として断定しないでください（式に触れるときは、復元された式であることを添えてください）。"
    "印は式ごとに付いています。同じ箇所でも印の無い式は原文の数式なので、復元扱いにしないでください。"
)


#: 第 25 周 第 11 波（IK-1158）: 表示中の教材に添える式の事実行は、教材本文と同じ区画に
#: つなげず、この見出しの別区画に置く（AI が「教材の末尾の行」として画面に無い行を指さない）。
OFFSCREEN_FORMULA_FACT_BLOCK_HEADING = "[この箇所の式（画面には出ない補助情報）]"

#: 上の別区画を置いたときだけ system に足す1文。区画が画面に無いことと、印字番号が
#: 教材・スライドに出ていないときは番号で呼ばないことを言う。
OFFSCREEN_FORMULA_FACT_INSTRUCTION = (
    f"【式の補助情報】文脈の「{OFFSCREEN_FORMULA_FACT_BLOCK_HEADING}」は学習者の画面には表示されていない"
    "補助情報です。この区画を「教材の末尾の行」「画面の印」として指し示さないでください。"
    "「式 (N)」のような印字番号が表示中の教材の本文に出ていないときは、番号で式を呼ばず、"
    "式の形（左辺の記号や式の中身）で指してください。"
)


def offscreen_formula_fact_block(line: str) -> str:
    """表示中の教材とは別の区画に置く式の事実行（無ければ ``""``）。"""
    line = str(line or "").strip()
    if not line:
        return ""
    return f"{OFFSCREEN_FORMULA_FACT_BLOCK_HEADING}\n{line}"


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


def _unnumbered_name(formula: dict) -> str:
    """印字番号の無い式を名指す語（式本文の先頭 → 定める記号）。名指せなければ ``""``。

    式本文の切り詰めは論文層の正本（``graph_paper_layer.schema.equation_latex_snippet``）を
    通す（``$…$`` の途中で括弧を割らない）。壊れた復元で本文を伏せた式（``latex_withheld``）は
    本文で名指さず、定める記号だけを使う。
    """
    from core.graph_paper_layer.schema import (
        equation_defined_symbols,
        equation_latex_snippet,
    )

    latex = "" if formula.get("latex_withheld") else str(formula.get("latex") or "").strip()
    if latex:
        snippet = equation_latex_snippet(
            {"source_extraction": {"latex": latex}}, UNNUMBERED_SNIPPET_MAX
        )
        if snippet:
            return f"{snippet} の式"
    symbols = equation_defined_symbols(formula)
    if symbols:
        return "、".join(f"${sym}$" for sym in symbols[:2]) + " を定める式"
    return ""


def _confirmed_by_original(formula: dict, confirmed: list[str]) -> bool:
    """復元式の LaTeX が、同じ論文の別の版（TeX 版など）の原文の式と一致するか（IK-0895 の規則の版またぎ）。"""
    if not confirmed:
        return False
    from core.learner_context_common import reconstruction_matches_source

    latex = formula.get("latex")
    return any(reconstruction_matches_source(latex, original) for original in confirmed)


def formula_fact_line(
    formulas: Any,
    *,
    referenced_ids: Any = None,
    confirmed_latex: Any = None,
    index: Any = None,
    document_id: Any = None,
) -> str:
    """式の事実を1行にする。事実が無ければ ``""``。

    第 25 周 第 5 波（IK-0997 / IK-1009）で**式単位**にした:

    - 印字番号のある式は「式 (N)」、復元式には ``［AI復元］`` を添える。
    - 印字番号の無い復元式は、式本文の先頭（``$…$``）か定める記号で 1 つずつ名指す
      （「$E=mc^2$ の式［AI復元］」）。名指せないものだけ「番号のない式［AI復元］」を
      1度書く（数えない）。
    - 印の判定は ``core.learner_context_common.equation_latex_is_reconstructed``
      （``annotate_reconstructed_formulas`` 経由）— TeX 経路で原文と一致する式には付かない。
    - 復元式と印の無い式が同じ箇所にあるときは「印の無い式は原文の数式です」を添える。
    - 印の説明（［AI復元］= …）は行ごとに繰り返さない（system の
      ``RECONSTRUCTED_FORMULA_INSTRUCTION`` に 1 回だけ置く）。
    - ``referenced_ids`` を渡すと、その id の式だけを対象にする（本文が参照した式）。
    - 第 25 周 第 6 波（IK-1009 の残り）: ``confirmed_latex`` は同じ論文の別の版（TeX 版）の
      **原文の式**の LaTeX。復元式の LaTeX がそのどれかと正規化後に一致すれば、原文と照合
      できた式として印を付けない（同じ式 (N) が教材では原文・PDF 版の抜粋では［AI復元］と
      食い違うのを、判定の材料を揃えて止める。判定は ``reconstruction_matches_source`` の 1 つ）。
    """
    # 第 25 周 第 6 波（IK-0895）: ``index``（生きた式の記録の索引）があれば印はその答え
    # （本文・講義・付録・部品の文脈と同じ ``learner_context_common.equation_is_reconstructed``）。
    items = annotate_reconstructed_formulas(
        _as_formula_list(formulas), index=index, document_id=document_id
    )
    confirmed = [str(x) for x in (confirmed_latex or []) if str(x or "").strip()]
    allowed = None
    if referenced_ids is not None:
        allowed = {str(x).strip() for x in referenced_ids if str(x or "").strip()}
    parts: list[str] = []
    has_reconstructed = False
    has_original = False
    unnamed_reconstructed = False
    mark = f"［{RECONSTRUCTED_EQUATION_MARK}］"
    for formula in items:
        if allowed is not None:
            fid = str(formula.get("id") or formula.get("equation_id") or "").strip()
            if fid not in allowed:
                continue
        printed = _printed_label(formula)
        reconstructed = bool(formula.get("reconstructed_mark")) and not _confirmed_by_original(
            formula, confirmed
        )
        if reconstructed:
            has_reconstructed = True
        else:
            has_original = True
        if printed:
            part = printed + (mark if reconstructed else "")
        elif reconstructed:
            name = _unnumbered_name(formula)
            if not name:
                unnamed_reconstructed = True
                continue
            part = name + mark
        else:
            continue
        if part not in parts:
            parts.append(part)
    if unnamed_reconstructed:
        parts.append(UNNUMBERED_FORMULA_WORD + mark)
    if not parts:
        return ""
    line = f"（{FORMULA_FACT_HEADING}: " + "、".join(parts)
    if has_reconstructed and has_original:
        line += f"。{ORIGINAL_FORMULAS_NOTE}"
    return line + "）"


def has_reconstructed_fact(line: str) -> bool:
    """事実行に復元式が含まれるか。"""
    return f"［{RECONSTRUCTED_EQUATION_MARK}］" in (line or "")
