"""テキストの切り詰め（excerpt）と TeX 数式判定の**単一正本**。

正本文書: `docs/features/element_context_presentation_redesign.md`
（CP5「切り詰めは1実装」/ RC11）。

背景: 要素文脈の投影は 60 / 80 / 120 / 220 字の素スライス（``text[:80]``）が5箇所、
``...`` 付きの独自 ``_short_excerpt`` が2実装に分裂しており、英文の単語途中で切れる
（``…from its spatial mea``）・TeX コマンドの途中で切れて KaTeX が壊れる、という
事故を各所で起こしていた。本モジュールがその唯一の実装で、**新しい素スライスを
書かない**（ガードレールで固定する）。

``looks_like_tex_math`` は元々 ``core/course_content_builder.py`` にあった判定の
移設先である（``course_content_builder`` 側は本モジュールからの再エクスポートに
なっており、旧名 ``_looks_like_tex_math`` も含めて import 面は不変）。移設理由は
``core/deliberation/labels.py`` が TeX 判定を必要とし、course_content_builder を
参照すると相互参照になるため。

本モジュールは純粋関数のみ（DB / FastAPI / LLM に依存しない）。
"""
from __future__ import annotations

import re

__all__ = [
    "excerpt",
    "first_sentence",
    "looks_like_tex_math",
    "normalize_whitespace",
]


# ── 切り詰め（excerpt）────────────────────────────────────────────────────────

_WHITESPACE_RE = re.compile(r"\s+")

# 文末記号。ASCII の ``.!?;`` は「直後が空白か文末」のときだけ文境界とみなす
# （``3.14`` / ``Fig. 5`` を文末と誤認しないため）。
_SENTENCE_END_RE = re.compile(r"[。．.!?；;！？]")
_ASCII_SENTENCE_ENDS = frozenset(".!?;")

# ``Fig. 5.2`` のような略語ピリオドを文末と誤認しないための短い denylist。
# 分野語ではなく一般的な学術英文の略語のみ（domain-independent）。
_ABBREVIATIONS = frozenset(
    {
        "fig", "figs", "eq", "eqs", "no", "nos", "cf", "vs", "ref", "refs",
        "sec", "secs", "tab", "tabs", "al", "eg", "ie", "approx", "resp",
        "dr", "prof", "mr", "ms", "st", "ch", "chap", "pp", "vol", "min", "max",
    }
)

# 語境界（この文字の**手前**で切る）。英文の空白に加え、日本語の読点・中黒・
# 括弧閉じなど「そこで切っても語が壊れない」文字を並べる。
_WORD_BOUNDARY_CHARS = frozenset(" \t,，、・;；:：/／|｜)）]］}｝〕》」』")

# 切り詰めの末尾に残すと不自然な文字（切った後に削る）。
_TRAILING_STRIP_CHARS = " \t,，、・:：;；/／|｜(（[［{｛〔《「『-—–"

# 文境界 / 語境界の採用条件: limit の 40% 以上を残すこと（極端に短い断片を作らない）。
_MIN_KEEP_RATIO = 0.4

_TEX_TOKEN_RE = re.compile(r"\\[A-Za-z]+")

_CJK_RE = re.compile(r"[　-ヿ一-鿿＀-￯]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]+")


def normalize_whitespace(text: object) -> str:
    """改行・連続空白を1個の半角空白へ潰し、前後を strip する。"""
    return _WHITESPACE_RE.sub(" ", str(text if text is not None else "")).strip()


def _is_sentence_boundary(text: str, index: int, char: str) -> bool:
    """``text[index]`` の終端記号が本当に文境界かどうか。

    ASCII の ``.!?;`` だけ追加検査する（日本語の ``。．！？`` は常に文境界）:
      * ``3.14`` のような数値表記でない
      * 直後が空白または文末（語中のピリオド・URL を弾く）
      * 直前の語が略語でない（``Fig.`` / ``et al.`` / ``e.g.``）
      * 中身のある文であること（``Fig. 3.`` のような見出し番号だけの断片を、
        図キャプションの「第1文」にしない）
    """
    if char not in _ASCII_SENTENCE_ENDS:
        return True
    prev = text[index - 1] if index else ""
    nxt = text[index + 1] if index + 1 < len(text) else ""
    if prev.isdigit() and nxt.isdigit():
        return False
    if nxt and not nxt.isspace():
        return False
    head = text[:index]
    token = re.split(r"[^A-Za-z]", head)[-1].lower() if head else ""
    if token in _ABBREVIATIONS:
        return False
    if not _CJK_RE.search(head):
        words = [w.lower() for w in _ASCII_WORD_RE.findall(head)]
        if not any(w not in _ABBREVIATIONS for w in words):
            return False
    return True


def _sentence_cut(window: str) -> int | None:
    """window 内の最後の文境界の直後の位置（無ければ None）。"""
    best: int | None = None
    for match in _SENTENCE_END_RE.finditer(window):
        if not _is_sentence_boundary(window, match.start(), match.group()):
            continue
        best = match.end()
    return best


def _word_cut(window: str) -> int | None:
    """window 内の最後の語境界の位置（境界文字の**手前**。無ければ None）。"""
    for index in range(len(window) - 1, 0, -1):
        if window[index] in _WORD_BOUNDARY_CHARS:
            return index
    return None


def _tex_safe_cut(body: str, cut: int) -> int:
    """TeX コマンドトークン（``\\alpha`` など）の途中で切らないよう位置を戻す。

    切り位置がトークンの内側なら、そのトークンの開始位置まで後退させる。後退の
    結果 0 以下になった場合は「安全に切れる位置が無い」ことを意味する。
    """
    if cut <= 0 or cut >= len(body):
        return cut
    start = body.rfind("\\", 0, cut)
    if start < 0:
        return cut
    match = _TEX_TOKEN_RE.match(body, start)
    if not match:
        return cut
    if match.start() < cut < match.end():
        return match.start()
    return cut


def excerpt(text: object, limit: int, *, ellipsis: str = "…") -> str:
    """``text`` を最大 ``limit`` 文字へ切り詰める（CP5 の唯一の実装）。

    手順:
      1. 空白正規化（改行・連続空白を1個の半角空白に）。
      2. ``limit`` 以下ならそのまま返す（``ellipsis`` は付けない）。
      3. 文境界（``。．.!?；;`` の直後）→ 語境界（空白・読点類の手前）→ 文字数、の
         順で切る位置を決める。文境界・語境界は ``limit`` の 40% 以上を残す場合のみ
         採用する（極端に短い断片にしない）。日本語のように空白が無い文は
         文境界 → 文字数へ自然に縮退する。
      4. 切った場合は必ず ``ellipsis`` を付ける。
      5. どの位置も TeX コマンドトークンの途中に掛かる場合は、そのトークンの手前へ
         後退させる。安全に切れる位置が無ければ**空文字**を返す（壊れた TeX を
         出すくらいなら何も出さない）。
    """
    body = normalize_whitespace(text)
    if not body:
        return ""
    try:
        max_len = int(limit)
    except (TypeError, ValueError):
        return ""
    if max_len <= 0:
        return ""
    if len(body) <= max_len:
        return body

    window = body[:max_len]
    floor = max(1, int(max_len * _MIN_KEEP_RATIO))

    candidates: list[int] = []
    sentence = _sentence_cut(window)
    if sentence is not None and sentence >= floor:
        candidates.append(sentence)
    word = _word_cut(window)
    if word is not None and word >= floor:
        candidates.append(word)
    candidates.append(max_len)

    for candidate in candidates:
        safe = _tex_safe_cut(body, candidate)
        if safe <= 0:
            continue
        head = body[:safe].rstrip(_TRAILING_STRIP_CHARS)
        if not head:
            continue
        return head + ellipsis
    return ""


def first_sentence(text: object) -> str:
    """先頭の1文を返す（文末記号を含む）。文境界が無ければ全体を返す。

    ``excerpt`` と同じ文境界規則（数値表記・略語ピリオドを文末にしない）を使う。
    """
    body = normalize_whitespace(text)
    if not body:
        return ""
    for match in _SENTENCE_END_RE.finditer(body):
        if not _is_sentence_boundary(body, match.start(), match.group()):
            continue
        return body[: match.end()].strip()
    return body


# ── TeX 数式判定（course_content_builder から移設した正本）────────────────────

_TEX_MATH_ENV_RE = re.compile(
    r"\\begin\{(?:aligned|align|equation|gather|multline|eqnarray|flalign|alignat"
    r"|split|cases|array|smallmatrix|[pbBvV]?matrix)\*?\}"
)
_TEX_COMMAND_RE = re.compile(r"\\[a-zA-Z]+")


def looks_like_tex_math(text: str) -> bool:
    """テキストが散文ではなく生の TeX 数式かどうかを判定する。

    equation_quote evidence や TeX ソース由来の equation block の本文を、
    文字数切り詰め（TeX を途中で壊す）や生テキスト表示の対象にしないための
    汎用判定。数式環境があれば即 TeX、それ以外は TeX コマンドの占有率で
    判定する（散文中に数式コマンドが少し混ざる程度では True にしない）。
    """
    t = str(text or "").strip()
    if not t:
        return False
    if _TEX_MATH_ENV_RE.search(t):
        return True
    commands = _TEX_COMMAND_RE.findall(t)
    if len(commands) < 2:
        return False
    covered = sum(len(cmd) + 1 for cmd in commands)
    return covered >= max(10, int(len(t) * 0.2))
