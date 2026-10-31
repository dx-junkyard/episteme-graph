"""画面文脈アダプター — 学習者が選択した箇所の逐語ブロック（Phase 4 §11.4）。

``selection_text`` は現在の位置（``api/schemas.py`` の独立フィールド）に残したまま、
**画面文脈とは別の第2ブロック**として当該ターンにだけ注入する。理由は §11.2:

- ``selection_text`` は**参照ではなく逐語テキスト**なので、「参照だけを渡す」
  ``screen_context``（SA1）に混ぜると条項が濁る。
- 痕跡側（``_learner_selected_anchor`` の ``evidence_quote``）は非改変。

本モジュールの責務は「逐語をそのまま載せる」ことと「**クライアント申告か、
サーバが本文と突き合わせて確かめた逐語か**を読み手に区別させる」ことの2つだけで、
DB も LLM も触らない（SA3）。**例外を外へ出さない**。
"""

from __future__ import annotations

import re

from core.text_hygiene import strip_control_sequences

from .schema import (
    MAX_SELECTION_TEXT_CHARS,
    SELECTION_BLOCK_HEADER,
    SELECTION_MATCH_CONFIRMED,
    SELECTION_MATCH_UNCONFIRMED,
)

#: 一致検査の正規化（空白・改行・全角空白の違いで「不一致」にしないため）。
#: 文字そのものは**書き換えず**、比較用のキーだけを作る（載せる逐語は原文のまま）。
_WHITESPACE_RE = re.compile(r"[\s　]+")


def _match_key(text: str) -> str:
    return _WHITESPACE_RE.sub("", text)


#: 選択に添える式の上限（第 25 周 第 10 波・SA7 の予算はコード定数）。
MAX_SELECTION_FORMULAS = 3
MAX_SELECTION_FORMULA_CHARS = 200
SELECTION_FORMULA_HEADING = "選択箇所に出てくる式（教材の式の記録）:"
_SYMBOL_KEY_RE = re.compile(r"[^0-9A-Za-z\u0370-\u03ff]+")
_TEX_COMMAND_RE = re.compile(r"\\(?:mathcal|mathrm|mathbf|mathit|operatorname|text|rm|bf|cal)\b")
_TEX_COMMAND_ANY_RE = re.compile(r"\\[A-Za-z]+")


def _symbol_key(text: str) -> str:
    text = _TEX_COMMAND_RE.sub("", text or "")
    text = _TEX_COMMAND_ANY_RE.sub("", text)
    return _SYMBOL_KEY_RE.sub("", text).casefold()


def selection_formula_lines(selection_text: str | None, formulas) -> list[str]:
    """選択テキストに定める記号（式の左辺）が現れる式の行を返す（決定論・最大 3 件）。

    ``formulas`` は表示中の教材の式の記録（``latex`` / 任意の ``label``（「式 (4)」等）/
    任意の ``mark``（「［AI復元］」等））。KaTeX の選択は記号が崩れて写るので、照合は英数字だけの
    キー（``L_{\\mathrm{TOV}}`` → ``ltov``）で行う。2 文字未満の記号は誤一致が多いので使わない。
    """
    try:
        key = _symbol_key(strip_control_sequences(str(selection_text or "")))
        if not key:
            return []
        out: list[str] = []
        for formula in formulas or []:
            if len(out) >= MAX_SELECTION_FORMULAS or not isinstance(formula, dict):
                break
            latex = strip_control_sequences(str(formula.get("latex") or "")).strip()
            if not latex or "=" not in latex:
                continue
            lhs = _symbol_key(latex.split("=", 1)[0])
            if len(lhs) < 2 or lhs not in key:
                continue
            label = str(formula.get("label") or "").strip()
            mark = str(formula.get("mark") or "").strip()
            line = f"{label}{mark}: ${latex}$" if label else f"${latex}${mark}"
            if len(line) > MAX_SELECTION_FORMULA_CHARS:
                continue
            if line not in out:
                out.append(line)
        return out
    except Exception:  # pragma: no cover - 防御的（SA2 fail-soft）
        return []


def render_selection_block(
    selection_text: str | None,
    material_text: str | None = None,
    formula_lines: list[str] | None = None,
) -> str:
    """選択逐語のブロックを描画する。選択が無ければ ``""``。

    - 制御シーケンスを除去してから載せる（TB1〜TB4。PDF 由来の untrusted 入力）。
    - ``MAX_SELECTION_TEXT_CHARS`` で切る（SA7: 予算はコード定数）。
    - ``material_text``（表示中の教材本文）に対する部分文字列一致で
      「一致を確認済み」／「一致は確認できていません」を必ず併記する。
      **不一致でも落とさない**（学習者が選んだ事実は情報 = 原則3）。
    """
    try:
        text = strip_control_sequences(str(selection_text or "")).strip()
        if not text:
            return ""
        text = text[:MAX_SELECTION_TEXT_CHARS]

        material = strip_control_sequences(str(material_text or ""))
        key = _match_key(text)
        matched = bool(key) and key in _match_key(material)

        lines = [line for line in (raw.strip() for raw in text.splitlines()) if line]
        if not lines:
            return ""
        quoted = [f"> {line}" for line in lines]
        marker = SELECTION_MATCH_CONFIRMED if matched else SELECTION_MATCH_UNCONFIRMED
        # 第 25 周 第 10 波（製品頭脳 A 4313）: 選択に出てくる式の LaTeX を添える（逐語の 600 字とは別枠）。
        formulas = [str(x) for x in (formula_lines or [])[:MAX_SELECTION_FORMULAS] if str(x or "").strip()]
        extra = [SELECTION_FORMULA_HEADING, *[f"- {line}" for line in formulas]] if formulas else []
        return "\n".join([SELECTION_BLOCK_HEADER, marker, *quoted, *extra])
    except Exception:  # pragma: no cover - 防御的（SA2 fail-soft）
        return ""
