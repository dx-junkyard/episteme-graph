"""表示用 LaTeX の**単一正本**: 式の切り詰め（``truncate_latex``）と、地の文に裸で混ざった
TeX の区切り付け（``delimit_bare_tex``）。

背景（2026-10-10・グラフレビューの数式崩れ）: 式の表示ラベルは本文の先頭 40 字を
**文字数で**切っていたため、``\\begin{cases}`` の ``\\end`` が欠ける・``\\bigg[`` の区切りが
欠ける・``\\frac1`` / ``\\overset`` の引数が欠ける、という KaTeX で描けない断片を作っていた。
例外コマンドを列挙して後から削る方式（旧 ``_trim_incomplete_tail``）は列挙漏れのたびに
壊れる。本モジュールは式を**原子**（コマンド + 引数・波括弧の群・``\\begin…\\end`` /
``\\left…\\right`` の対・上付き下付きを伴う原子）に分け、**原子の境目でしか切らない**。
元の式が描けるなら、切った断片も描ける。

もう一つの原因は、解析結果の地の文（前提・導出の理由・定義の引用・理論対象）が
``m_{\\Lambda_b}`` のような TeX を ``$`` なしで含むことで、画面の描画器（``$…$`` だけを
数式として描く）が生の TeX を出していた。``delimit_bare_tex`` は ``$…$`` の外にある
TeX らしい語の並びだけを ``$…$`` で囲む（既存の区切りには触れない・波括弧が釣り合わない
並びは囲まない＝壊れた数式を作らない）。

純粋関数のみ（DB / FastAPI / LLM 非依存）。
"""
from __future__ import annotations

import re

__all__ = [
    "LATEX_ARG_COMMANDS",
    "PROSE_MATH_KEYS",
    "delimit_bare_tex",
    "looks_like_bare_tex",
    "truncate_latex",
]

#: 引数を取る LaTeX コマンドと必須引数の数（引数は ``{…}`` か 1 トークン）。
#: ここに無いコマンドも、直後に続く ``{…}`` は同じ原子に含めるので、引数と切り離されない。
LATEX_ARG_COMMANDS: dict[str, int] = {
    "frac": 2, "dfrac": 2, "tfrac": 2, "cfrac": 2, "binom": 2, "dbinom": 2, "tbinom": 2,
    "overset": 2, "underset": 2, "stackrel": 2,
    "sqrt": 1, "hat": 1, "widehat": 1, "bar": 1, "overline": 1, "underline": 1, "tilde": 1,
    "widetilde": 1, "vec": 1, "dot": 1, "ddot": 1, "check": 1, "breve": 1, "acute": 1, "grave": 1,
    "mathring": 1, "overrightarrow": 1, "overleftarrow": 1, "xrightarrow": 1, "xleftarrow": 1,
    "mathrm": 1, "mathbf": 1, "mathit": 1, "mathcal": 1, "mathbb": 1, "mathsf": 1, "mathtt": 1,
    "mathfrak": 1, "mathscr": 1, "boldsymbol": 1, "bm": 1, "pmb": 1, "text": 1, "textrm": 1,
    "textbf": 1, "textit": 1, "mbox": 1, "operatorname": 1, "overbrace": 1, "underbrace": 1,
    # 大きさ指定の区切り（``\\bigg[``）。引数は区切り記号 1 トークン。
    "big": 1, "Big": 1, "bigg": 1, "Bigg": 1, "bigl": 1, "Bigl": 1, "biggl": 1, "Biggl": 1,
    "bigr": 1, "Bigr": 1, "biggr": 1, "Biggr": 1, "bigm": 1, "Bigm": 1, "biggm": 1, "Biggm": 1,
}

#: 任意引数 ``[…]`` を取りうるコマンド。
_OPTIONAL_ARG_COMMANDS = frozenset({"sqrt", "xrightarrow", "xleftarrow"})

#: 切った断片の末尾に残しても意味のない語（空白・区切りの記号）。
_TRAILING_NOISE_RE = re.compile(r"(?:\s|\\[,;:!> ]|\\q?quad(?![A-Za-z])|~|,|\\cdots|\\ldots|\\dots)+$")


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _match_group(text: str, start: int, open_char: str = "{", close_char: str = "}") -> int:
    """``text[start] == open_char`` の対応する閉じの直後の位置。釣り合わなければ ``len(text)``。"""
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text)


def _command_at(text: str, index: int) -> tuple[str, int]:
    """``text[index] == "\\\\"`` のコマンド名とその直後の位置。"""
    match = re.match(r"\\([A-Za-z]+)\*?|\\(.)", text[index:], re.DOTALL)
    if not match:
        return "", index + 1
    return (match.group(1) or match.group(2) or ""), index + match.end()


def _token_end(text: str, index: int) -> int:
    """1 トークン（群・コマンド・1 文字）の終わり。"""
    index = _skip_space(text, index)
    if index >= len(text):
        return index
    if text[index] == "{":
        return _match_group(text, index)
    if text[index] == "\\":
        return _command_at(text, index)[1]
    return index + 1


def _env_end(text: str, index: int, name: str) -> int:
    """``\\begin{name}`` の本体の始まり ``index`` から、対応する ``\\end{name}`` の直後まで。"""
    pattern = re.compile(r"\\(begin|end)\{" + re.escape(name) + r"\}")
    depth = 1
    for match in pattern.finditer(text, index):
        depth += 1 if match.group(1) == "begin" else -1
        if depth == 0:
            return match.end()
    return len(text)


def _left_right_end(text: str, index: int) -> int:
    """``\\left`` の区切りの直後 ``index`` から、対応する ``\\right`` + 区切りの直後まで。"""
    depth = 1
    pattern = re.compile(r"\\(left|right)(?![A-Za-z])")
    for match in pattern.finditer(text, index):
        depth += 1 if match.group(1) == "left" else -1
        if depth == 0:
            return _token_end(text, match.end())
    return len(text)


def _atom_end(text: str, index: int) -> int:
    """``index`` から始まる原子の終わり（上付き・下付きは呼び出し側で連結する）。"""
    char = text[index]
    if char == "{":
        return _match_group(text, index)
    if char != "\\":
        return index + 1
    name, after = _command_at(text, index)
    if name == "begin":
        brace = _skip_space(text, after)
        if brace < len(text) and text[brace] == "{":
            close = _match_group(text, brace)
            env = text[brace + 1:close - 1]
            return _env_end(text, close, env)
        return after
    if name == "left":
        return _left_right_end(text, _token_end(text, after))
    arity = LATEX_ARG_COMMANDS.get(name, 0)
    position = after
    if name in _OPTIONAL_ARG_COMMANDS:
        probe = _skip_space(text, position)
        if probe < len(text) and text[probe] == "[":
            position = _match_group(text, probe, "[", "]")
    for _ in range(arity):
        position = _token_end(text, position)
    # 引数の数が分からないコマンドでも、直後に続く群は同じ原子にする（切り離すと描けない）。
    while True:
        probe = position
        if probe < len(text) and text[probe] == "{":
            position = _match_group(text, probe)
            continue
        return position


def _atoms(text: str) -> list[tuple[int, int]]:
    """式を原子の区間 ``(start, end)`` の列に分ける（空白は区間の外）。"""
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        if text[index] in "^_" and spans:
            # 上付き・下付きは直前の原子に連結する（``x^`` で切らない）。
            end = _token_end(text, index + 1)
            spans[-1] = (spans[-1][0], max(end, index + 1))
            index = spans[-1][1]
            continue
        end = max(_atom_end(text, index), index + 1)
        spans.append((index, end))
        index = end
    return spans


def truncate_latex(text: str, limit: int, *, hard_cap: int | None = None) -> tuple[str, bool]:
    """式 ``text`` を ``limit`` 字前後で**原子の境目で**切る。``(断片, 切ったか)`` を返す。

    先頭の原子だけで ``limit`` を超えるときは ``hard_cap``（既定 ``limit * 3``）まで先頭の
    原子を丸ごと残す。それも超えるなら ``("", True)``（描けない断片を出すより、呼び出し側の
    別の表示へ落とす）。末尾に残った空白・区切り記号（``\\,`` / ``,`` / ``\\quad``）は落とす。
    """
    body = str(text or "").strip()
    if not body:
        return "", False
    if len(body) <= limit:
        return body, False
    cap = hard_cap if hard_cap is not None else limit * 3
    end = 0
    for start, stop in _atoms(body):
        if stop <= limit:
            end = stop
            continue
        if end == 0 and stop <= cap:
            end = stop
        break
    if end == 0:
        return "", True
    if end >= len(body):
        return body, False
    head = _TRAILING_NOISE_RE.sub("", body[:end]).rstrip()
    if not head:
        return "", True
    return head, True


# ---------------------------------------------------------------------------
# 地の文の裸の TeX を ``$…$`` で囲む
# ---------------------------------------------------------------------------

#: 地の文（解析結果の英語・日本語の説明文）として表示する教員向け DTO のキー。
#: ``latex`` / ``symbol`` / ``*_symbols`` は画面側が ``$…$`` で包むので含めない。
PROSE_MATH_KEYS: frozenset[str] = frozenset({
    "reason", "review_reason", "text", "definition_quote", "theory_object", "label",
    "description", "summary", "body", "statement", "subtitle", "display_label",
    "input_labels", "output_labels", "equation_labels", "facts",
})

_DOLLAR_SEGMENT_RE = re.compile(r"(\$\$[\s\S]+?\$\$|\$[^$\n]+?\$|\\\([\s\S]+?\\\))")
_COMMAND_RE = re.compile(r"\\[A-Za-z]+")
#: ``m_B`` / ``R_{H_c}`` / ``k^{-1}`` / ``(2\pi)^3`` のような添字・指数付きの記号。
#: 添字の土台は 1〜2 字か閉じ括弧（``zero_recoil_limit`` のような識別子は数式にしない）。
_SCRIPT_RE = re.compile(r"(?:^|[^A-Za-z])[A-Za-z]{1,2}[_^]|[)\]}][_^]|\d[_^]")
_IDENTIFIER_RE = re.compile(r"[A-Za-z]{3,}(?:_[A-Za-z0-9]+)+")
#: 式・図・表・節の参照（``(eq_1)`` / ``eq:F2`` / ``fig_3``）は数式ではない。
_REFERENCE_WORD_RE = re.compile(r"(?:^|[^A-Za-z])(?:eq|eqs|fig|tab|sec|app)[_:]", re.IGNORECASE)
_OPERATOR_WORDS = frozenset({"=", "+", "-", "−", "<", ">", "/", "≈", "≃", "~", "|"})
_TRAILING_PUNCT = ".,;:"
_URLISH_RE = re.compile(r"https?://|www\.", re.IGNORECASE)


def _is_cjk(char: str) -> bool:
    """和文の文字・和文の約物（``、`` ``：`` を含む）か。数式の語はこれをまたがない。"""
    code = ord(char)
    return (
        0x3000 <= code <= 0x30FF      # 和文の約物・かな
        or 0x3400 <= code <= 0x9FFF   # 漢字
        or 0xF900 <= code <= 0xFAFF
        or 0xFF00 <= code <= 0xFFEF   # 全角の英数・約物
    )


def _words(text: str) -> list[tuple[int, int]]:
    """波括弧の外の空白・和文の文字で区切った語の区間（``{\\cal {H}}`` を割らない）。

    和文は語の区切りとして扱う（``R_δ(λ)、定義:`` を 1 語にして ``$R_δ(λ)、定義$:`` と
    和文ごと囲んでいた — judge-deep-07 ②）。波括弧の中の和文（``\\text{…}``）は割らない。
    """
    spans: list[tuple[int, int]] = []
    depth = 0
    start: int | None = None
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            if start is None:
                start = index
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        if (char.isspace() or _is_cjk(char)) and depth == 0:
            if start is not None:
                spans.append((start, index))
                start = None
        elif start is None:
            start = index
        index += 1
    if start is not None:
        spans.append((start, len(text)))
    return spans


def _is_math_word(word: str) -> bool:
    core = word.rstrip(_TRAILING_PUNCT)
    if not core or _IDENTIFIER_RE.fullmatch(core) or _REFERENCE_WORD_RE.search(core):
        return False
    if _COMMAND_RE.search(core):
        return True
    # 数値に付いた空白指定（``20\\,Mpc``）。
    if re.search(r"\\[,;:!]", core) and re.search(r"\d", core):
        return True
    return bool(_SCRIPT_RE.search(core))


def _is_operator_word(word: str) -> bool:
    return word in _OPERATOR_WORDS or bool(re.fullmatch(r"\\(?:to|approx|simeq|sim|equiv|leq|geq|le|ge|ll|gg|times|cdot|propto|neq|ne|rightarrow|leftarrow|mid)", word))


def _balanced(text: str) -> bool:
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
        index += 1
    return depth == 0


def looks_like_bare_tex(text: object) -> bool:
    """``$…$`` の外に TeX らしい語（コマンド・添字付きの記号）があるか。"""
    raw = str(text or "")
    outside = _DOLLAR_SEGMENT_RE.sub(" ", raw)
    return any(_is_math_word(outside[s:e]) for s, e in _words(outside))


def _delimit_plain(text: str) -> str:
    words = _words(text)
    if not words:
        return text
    out: list[str] = []
    cursor = 0
    index = 0
    while index < len(words):
        start, stop = words[index]
        word = text[start:stop]
        if not _is_math_word(word):
            index += 1
            continue
        # 語の並びを伸ばす: 数式の語・演算子・演算子の直後の短い項（``\\to 1``）。
        run_end_index = index
        last_math_index = index
        probe = index + 1
        while probe < len(words) and not text[words[run_end_index][0]:words[run_end_index][1]].endswith(tuple(_TRAILING_PUNCT)):
            candidate = text[words[probe][0]:words[probe][1]]
            previous = text[words[probe - 1][0]:words[probe - 1][1]]
            if _is_math_word(candidate):
                run_end_index = last_math_index = probe
            elif _is_operator_word(candidate):
                run_end_index = probe
            elif _is_operator_word(previous) and re.fullmatch(r"[A-Za-z0-9.]{1,3}[.,;:]?", candidate):
                run_end_index = last_math_index = probe
            else:
                break
            probe += 1
        run_end_index = last_math_index
        run_start = start
        run_stop = words[run_end_index][1]
        run = text[run_start:run_stop]
        trailing = ""
        while run and run[-1] in _TRAILING_PUNCT:
            trailing = run[-1] + trailing
            run = run[:-1]
        if run and "$" not in run and _balanced(run) and not _URLISH_RE.search(run):
            out.append(text[cursor:run_start])
            out.append(f"${run}${trailing}")
            cursor = run_stop
        index = run_end_index + 1
    out.append(text[cursor:])
    return "".join(out)


def delimit_bare_tex(text: object) -> str:
    """地の文の ``$…$`` の外にある TeX の語の並びを ``$…$`` で囲む（入力が str 以外ならそのまま str 化）。

    - 既存の ``$…$`` / ``$$…$$`` / ``\\(…\\)`` の中には触れない。
    - ``$`` の数が奇数の文（区切りがすでに壊れている）は触らない（推測で直さない）。
    - 波括弧が釣り合わない並び・URL は囲まない。
    """
    raw = str(text if text is not None else "")
    if "\\" not in raw and "_" not in raw and "^" not in raw:
        return raw
    if raw.replace("\\$", "").count("$") % 2 == 1:
        return raw
    parts = _DOLLAR_SEGMENT_RE.split(raw)
    return "".join(
        part if index % 2 == 1 else _delimit_plain(part)
        for index, part in enumerate(parts)
    )
