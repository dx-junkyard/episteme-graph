"""応答の骨格（Dialogue Response Shape）— 学習チャット応答の区画をサーバが組み立てる純関数群。

正本: docs/features/dialogue_response_shape_design.md（RS1〜RS5）。

応答の「形」（答え → 確認 → 問い返し → 前提の逆質問）を LLM の自制とプロンプトへの
固定文の継ぎ足しに任せず、生成後にサーバが決定論で区画へ分ける。プロンプトは区画の
中身を埋めるだけ。

  RS1 先に答える: 本文（答え）を必ず先頭に置く。
  RS2 確認は後ろ: 確認（鏡の言い直し・前提の逆質問・帰属確認カード）は答えの後ろに置き、
      1 往復に確認の区画は 1 つまで（前提の逆質問 か 鏡 か 帰属確認カード）。
  RS3 問い返しは 1 つまで: 末尾に連なる問いは最後の 1 つだけ残す。確認（逆質問・鏡）が
      あるターンでは問い返しを置かない（確認そのものが唯一の問い）。
  RS4 鏡は核心語のみ: 鏡が映すのは学習者の発話の核心の語句で、同意・挨拶・前置きは映さない。
      核心語が残らなければ鏡を出さない（偽の鏡を作らない）。
  RS5 保存文の形は不変: 保存する回答本文は「本文 → 問い返し → ``\\n\\n---\\n\\n`` + 目印 + 逆質問」
      の形を保つ（履歴の判定器 4 本と既存テストがこの形に依存する）。

FastAPI / sqlalchemy / LLM を import しない（テスタビリティ確保）。数値（件数・率）は
DTO に載せない。学習チャットの route（``api/routes/learning.py``）の固有定数（逆質問の
目印など）は引数で受け取る（core から route を import しない）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.discuss.mirroring import extract_mirror

__all__ = [
    "ResponseShape",
    "SHAPE_DTO_KEYS",
    "GATE_SEPARATOR",
    "assemble",
    "mirror_core",
    "previous_correction_fact",
    "render_answer_text",
    "shape_dto",
    "split_closing_question",
]

#: 保存文で本文と前提の逆質問を区切る区切り（RS5。履歴の判定器と既存テストが依存する）。
GATE_SEPARATOR = "\n\n---\n\n"

#: 応答 DTO ``LearningChatResponse.shape`` のキー（固定 3 つ・数値なし）。
SHAPE_DTO_KEYS: tuple[str, ...] = ("closing_question", "has_gate", "mirror_kept")

# ---------------------------------------------------------------------------
# 鏡の核心語（RS4）
# ---------------------------------------------------------------------------

#: 同意・挨拶・前置きの語（鏡に映さない）。学習者の発話の先頭に来る定型の相づち。
_PREAMBLE_PHRASES: tuple[str, ...] = (
    "はい", "ええ", "うん", "そうですね", "そうですか", "なるほど", "わかりました",
    "分かりました", "了解です", "了解しました", "了解", "ありがとうございます",
    "ありがとう", "確かに", "たしかに", "そのとおりです", "そのとおり", "その通りです",
    "その通り", "おっしゃるとおり", "おっしゃる通り", "こんにちは", "すみません",
    "それでは", "では", "じゃあ", "えっと", "えーと",
    "ok", "okay", "yes", "sure", "right", "thanks", "thank you", "i see", "got it",
)
#: 前置きの後ろに続く区切り（読点・感嘆・空白など）。日本語の前置きはこの文字か文末が
#: 続くときだけ剥がす（語の途中で切らない）。
_PREAMBLE_SEP = "、,，。.!！?？ 　…~〜ー\n\t"
#: 鏡の引用が「発話全体の丸写し」とみなす長さ（これより長い発話を丸ごと引用した鏡は
#: 核心を選んでいない）。
_WHOLE_MESSAGE_QUOTE_MIN_CHARS = 40
_MIN_QUOTE_CHARS = 2
# mirroring.py の _QUOTE_RE と同じ 3 種の引用（「」/ “” / ""）。
_QUOTE_RE = re.compile(r"「([^「」]+)」|“([^“”]+)”|\"([^\"]+)\"")
_MIRROR_SPAN_RE = re.compile(r"〔鏡〕(.+?)〔/鏡〕", re.DOTALL)


def _strip_preamble(text: str) -> str:
    """先頭の同意・挨拶・前置き（とその後ろの区切り）を繰り返し剥がす。"""
    rest = str(text or "").strip()
    changed = True
    while changed and rest:
        changed = False
        lowered = rest.lower()
        for phrase in sorted(_PREAMBLE_PHRASES, key=len, reverse=True):
            if not lowered.startswith(phrase):
                continue
            tail = rest[len(phrase):]
            if phrase.isascii():
                # 英字の語は語境界を要求する（"yes" で "yesterday" を剥がさない）。
                if tail[:1].isalnum() or tail[:1] == "_":
                    continue
            elif tail and tail[0] not in _PREAMBLE_SEP:
                # 日本語の語は後ろに区切り（読点・感嘆・空白など）か文末を要求する
                # （「では」で「ではなく」、「うん」で「うんどう」、「確かに」で
                # 「確かに存在する」を剥がさない）。
                continue
            stripped = tail.lstrip(_PREAMBLE_SEP)
            if stripped != rest:
                rest = stripped
                changed = True
            break
    return rest.strip()


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _core_quote(quote: str, learner_message: str) -> str | None:
    """1 つの引用から核心語を取り出す（逐語部分文字列のまま）。無ければ None。"""
    q = str(quote or "").strip()
    core = _strip_preamble(q)
    if len(core) < _MIN_QUOTE_CHARS or core not in str(learner_message or ""):
        return None
    message_core = _strip_preamble(learner_message)
    if (
        len(_norm_ws(message_core)) > _WHOLE_MESSAGE_QUOTE_MIN_CHARS
        and _norm_ws(core) == _norm_ws(message_core)
    ):
        # 長い発話の丸写しは核心を選んでいない。
        return None
    return core


def _rebuild_mirror_text(mirror_text: str, learner_message: str) -> str | None:
    """鏡文の引用を核心語へ置き換える。核心語が 1 つも残らなければ None。"""
    text = str(mirror_text or "")
    matches = list(_QUOTE_RE.finditer(text))
    if not matches:
        return None
    joiners = "、,，と や"
    kept = 0
    pieces: list[str] = []
    last = 0
    skip_joiner = False
    for match in matches:
        before = text[last:match.start()]
        if skip_joiner:
            before = before.lstrip(joiners)
            skip_joiner = False
        raw = next((g for g in match.groups() if g), "")
        core = _core_quote(raw, learner_message)
        if core is None:
            # 引用ごと落とし、引用どうしをつないでいた接続（「、」「と」）も落とす。
            if kept > 0 and not before.strip(joiners):
                pieces.append("")
            else:
                pieces.append(before)
                skip_joiner = True
        else:
            pieces.append(before)
            kept += 1
            whole = match.group(0)
            pieces.append(f"{whole[0]}{core}{whole[-1]}")
        last = match.end()
    tail = text[last:]
    if skip_joiner and kept > 0:
        tail = tail.lstrip("、,，")
    pieces.append(tail)
    if kept == 0:
        return None
    return "".join(pieces).strip()


def mirror_core(mirror: dict | str | None, learner_message: str) -> dict | None:
    """鏡を学習者の発話の**核心語**だけを映す形にする（RS4 / IK-0575）。

    - 同意・挨拶・前置き（「はい」「そうですね」等）だけの引用は落とす。
    - 前置きで始まる引用は前置きを剥がした核心語に置き換える（逐語部分文字列のまま）。
    - 長い発話（前置きを除いて 40 字超）を丸ごと写した引用は核心を選んでいないので落とす。
    - 核心語が 1 つも残らなければ None（偽の鏡を作らない）。

    ``mirror`` は ``{"text": ...}``（``extract_mirror`` の戻り値）か鏡文そのもの。冪等。
    """
    if mirror is None:
        return None
    text = mirror.get("text") if isinstance(mirror, dict) else mirror
    rebuilt = _rebuild_mirror_text(str(text or ""), learner_message)
    if rebuilt is None:
        return None
    return {"text": rebuilt}


# ---------------------------------------------------------------------------
# 末尾の問い（RS3）
# ---------------------------------------------------------------------------

_QUESTION_END = ("？", "?")
# 行末のアクション目印（ドリルダウン）。問いの判定から外し、区画の最後に残す。
_ACTION_LINE_RE = re.compile(
    r"^\s*(?:[-*・]\s*)?\[(?:ACTION_BUTTON:[^\]]*|[^\]]*について詳しく聞く|Ask more about[^\]]*)\]\s*$"
)
_LIST_OR_QUOTE_LINE_RE = re.compile(r"^\s*(?:[-*・>|]|\d+[.)．]|[（(]?\d+[)）])")
_SENTENCE_BOUNDARY = "。！？!?\n"


def _split_action_tail(text: str) -> tuple[str, str]:
    """末尾に連なるアクション目印の行（と空行）を本文から分ける。"""
    lines = str(text or "").rstrip().split("\n")
    tail: list[str] = []
    while lines and (not lines[-1].strip() or _ACTION_LINE_RE.match(lines[-1])):
        tail.insert(0, lines.pop())
    # 先頭の空行は捨てる（区画の結合で改めて空行を入れる）。
    while tail and not tail[0].strip():
        tail.pop(0)
    return "\n".join(lines).rstrip(), "\n".join(tail).strip()


def _pop_trailing_question(text: str) -> tuple[str, str] | None:
    """本文末尾の問いの文を 1 つ取り出す。取り出せなければ None。"""
    t = str(text or "").rstrip()
    if not t or not t.endswith(_QUESTION_END):
        return None
    if t.count("```") % 2 == 1:
        return None
    last_line = t.rsplit("\n", 1)[-1]
    if _LIST_OR_QUOTE_LINE_RE.match(last_line):
        # 箇条書き・引用・表の中の問いには触れない。
        return None
    if last_line.strip().startswith("$$") or last_line.count("$") % 2 == 1:
        return None
    start = -1
    for i in range(len(t) - 2, -1, -1):
        ch = t[i]
        if ch in _SENTENCE_BOUNDARY:
            start = i
            break
        # 英文のピリオド + 空白（"... done. Why?"）も文の境界。
        if ch == "." and i + 1 < len(t) and t[i + 1] == " ":
            start = i
            break
    sentence = t[start + 1:].strip()
    rest = t[: start + 1].rstrip()
    if not sentence or len(sentence) < 2:
        return None
    # 開き括弧だけが残る（引用の途中で切れた）文は問いとして扱わない。
    if sentence.count("「") != sentence.count("」") or sentence.count("（") != sentence.count("）"):
        return None
    return rest, sentence


def split_closing_question(body: str) -> tuple[str, list[str]]:
    """本文末尾に連なる問いの文を取り出す（RS3）。

    Returns:
        ``(body_without, questions)``。``questions`` は本文中の出現順。末尾のアクション目印
        （``[ACTION_BUTTON: ...]`` / ``[〇〇について詳しく聞く]``）は問いの判定から外して
        ``body_without`` の末尾に残す。箇条書き・引用・コード・数式の中の問いには触れない。
    """
    prose, action_tail = _split_action_tail(body)
    questions: list[str] = []
    rest = prose
    while True:
        popped = _pop_trailing_question(rest)
        if popped is None:
            break
        rest, sentence = popped
        questions.insert(0, sentence)
    body_without = rest.rstrip()
    if action_tail:
        body_without = (body_without + "\n\n" + action_tail).strip() if body_without else action_tail
    return body_without, questions


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResponseShape:
    """応答の区画。``body`` は答えの本文（鏡のマーカーは含み得る・アクション目印は除く）。"""

    body: str
    mirror: dict | None
    closing_question: str | None
    gate_text: str | None
    allows_anchor_confirm: bool
    dropped_questions: tuple[str, ...] = ()
    actions_tail: str = ""
    gate_marker: str = ""
    passthrough: bool = False
    #: 保存・応答用の本文（逆質問を除く）。``assemble`` が元の回答本文の位置で切り貼りして
    #: 作る（何も落とさないときは元の本文そのもの = バイト一致）。``None`` のときだけ
    #: ``render_answer_text`` は区画から組み立て直す（区画を直接作った呼び出しの互換）。
    spliced_text: str | None = None


def _apply_mirror_core(answer: str, learner_message: str) -> tuple[str, dict | None]:
    """本文中の 〔鏡〕…〔/鏡〕 を核心語だけの鏡に置き換える（保存文にも反映する）。

    鏡が verbatim 検査に不合格なら ``extract_mirror`` と同じく鏡扱いしない（本文は
    そのまま返し、後段の ``extract_mirror`` がマーカーだけを剥がす）。核心語が残らない
    鏡は本文から取り除く（映すものの無い鏡を出さない）。
    """
    match = _MIRROR_SPAN_RE.search(answer)
    if match is None:
        return answer, None
    _clean, extracted = extract_mirror(answer, learner_message)
    if extracted is None:
        return answer, None
    narrowed = mirror_core(extracted, learner_message)
    if narrowed is None:
        without = (answer[: match.start()].rstrip() + "\n\n" + answer[match.end():].lstrip()).strip()
        return without, None
    replaced = answer[: match.start()] + "〔鏡〕" + narrowed["text"] + "〔/鏡〕" + answer[match.end():]
    return replaced, narrowed


def _splice_out_questions(text: str, questions: list[str], drop: set[int]) -> str:
    """``text`` から末尾の問いのうち ``drop`` の添字のものを元の位置で取り除く。

    ``questions`` は ``split_closing_question(text)`` の戻り値（本文中の出現順）。問いの
    後ろの空白ごと取り除き（末尾の問いなら前の空白も）、それ以外（残す問い・アクション目印・
    本文との改行）は元のまま残す。
    """
    if not drop:
        return text
    prose, _actions = _split_action_tail(text)
    # ``prose`` は ``text`` の先頭部分（行を元のまま連結し末尾の空白だけ落としたもの）。
    head_len = len(prose) if text.startswith(prose) else len(text)
    head, tail = text[:head_len], text[head_len:]
    spans: list[tuple[int, int]] = []
    end = len(head)
    for q in reversed(questions):
        pos = head.rfind(q, 0, end)
        if pos < 0:
            return text  # 位置が取れないなら何も落とさない（本文を壊さない）
        spans.insert(0, (pos, pos + len(q)))
        end = pos
    ws = " \t\u3000\n"
    out = head
    for index in sorted(drop, reverse=True):
        start, stop = spans[index]
        # 問いの後ろの空白ごと取り除く（前の区切り = 本文との改行は残す）。
        while stop < len(out) and out[stop] in ws:
            stop += 1
        if stop >= len(out):
            # 末尾の問いを落とすときは前の空白も落とす（本文の後ろに空白を残さない）。
            while start > 0 and out[start - 1] in ws:
                start -= 1
        out = out[:start] + out[stop:]
    return out + tail


def assemble(
    *,
    answer: str,
    learner_message: str,
    is_discuss: bool,
    gate_text: str | None,
    degraded: bool,
    gate_marker: str = "",
) -> ResponseShape:
    """生成した回答本文を区画に分ける（RS1〜RS4）。

    順序は「本文 → （鏡の確認）→ 問い返し → 前提の逆質問」。

    - ``gate_text`` があるとき: 逆質問が唯一の問い。末尾の問い返しは落とし、帰属確認カードも出さない。
    - 鏡（核心語が残ったもの）があるとき: 鏡の確認が唯一の問い。末尾の問い返しは落とす。
    - どちらも無いとき: 末尾に連なる問いは最後の 1 つだけ残す。
    - 本文が問いだけで、落とすと空になるときは落とさない（答えを消さない）。
    - ``degraded``（固定文）は組み替えない。
    """
    answer = str(answer or "")
    gate = str(gate_text) if gate_text else None
    if degraded:
        return ResponseShape(
            body=answer,
            mirror=None,
            closing_question=None,
            gate_text=None,
            allows_anchor_confirm=True,
            passthrough=True,
        )
    mirror: dict | None = None
    if is_discuss:
        answer, mirror = _apply_mirror_core(answer, learner_message)
    body_without, questions = split_closing_question(answer)
    prose, actions_tail = _split_action_tail(body_without)
    closing: str | None = None
    dropped: tuple[str, ...] = ()
    drop_index: set[int] = set()
    if questions and not prose.strip():
        # 本文が問いだけ: 落とすと答えが消えるので組み替えない。
        prose, actions_tail = _split_action_tail(answer)
    elif questions and (gate is not None or mirror is not None):
        dropped = tuple(questions)
        drop_index = set(range(len(questions)))
    elif questions:
        closing = questions[-1]
        dropped = tuple(questions[:-1])
        drop_index = set(range(len(questions) - 1))
    # 保存文は元の本文の位置で切り貼りする（何も落とさなければ元の本文そのもの）。
    spliced = _splice_out_questions(answer, questions, drop_index)
    return ResponseShape(
        body=prose.strip(),
        mirror=mirror,
        closing_question=closing,
        gate_text=gate,
        allows_anchor_confirm=gate is None and mirror is None,
        dropped_questions=dropped,
        actions_tail=actions_tail,
        gate_marker=str(gate_marker or ""),
        spliced_text=spliced,
    )


def render_answer_text(shape: ResponseShape) -> str:
    """区画から保存・応答用の本文を組み立てる（RS5: 保存文の形は不変）。

    ``本文 (+ 問い返し) (+ アクション目印) (+ "\\n\\n---\\n\\n" + 目印 + 逆質問)``。
    ``assemble`` の結果は元の回答本文の位置で切り貼りした本文を使う（落とすものが無ければ
    元の本文とバイト一致・区切りを組み直さない）。逆質問は末尾の空白を落としてから区切りで足す。
    """
    if shape.passthrough:
        return shape.body
    if shape.spliced_text is not None:
        # 元の本文の位置で切り貼りした本文（何も落とさなければ元の本文とバイト一致）。
        if not shape.gate_text:
            return shape.spliced_text
        return shape.spliced_text.rstrip() + GATE_SEPARATOR + shape.gate_marker + shape.gate_text
    parts = [shape.body] if shape.body else []
    if shape.closing_question:
        parts.append(shape.closing_question)
    if shape.actions_tail:
        parts.append(shape.actions_tail)
    text = "\n\n".join(parts)
    if shape.gate_text:
        text = text + GATE_SEPARATOR + shape.gate_marker + shape.gate_text
    return text


def shape_dto(shape: ResponseShape, *, mirror: dict | None = None) -> dict | None:
    """応答 DTO（``LearningChatResponse.shape``）。固定 3 キー・数値なし。固定文では None。"""
    if shape.passthrough:
        return None
    return {
        "closing_question": shape.closing_question,
        "has_gate": bool(shape.gate_text),
        "mirror_kept": mirror is not None,
    }


# ---------------------------------------------------------------------------
# 前の往復の訂正の持ち越し（IK-0576）
# ---------------------------------------------------------------------------

_CORRECTION_KEYWORDS = ("訂正", "より正確です", "誤解")
_CORRECTION_MAX_CHARS = 200
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])\s*|\n+")


def previous_correction_fact(history: list | None) -> str | None:
    """直前の assistant 往復が訂正を含んでいれば、その文を逐語で返す（200 字まで）。

    直前の assistant 発話だけを見る（それより前の訂正は既に会話の流れで扱われた）。
    訂正の語（訂正 / より正確です / 誤解）を含む文を出現順に連結し、200 字で切る。
    見つからなければ None。
    """
    if not history:
        return None
    last_assistant = None
    for turn in reversed(list(history)):
        if isinstance(turn, dict) and turn.get("role") == "assistant":
            last_assistant = str(turn.get("content") or "")
            break
    if not last_assistant or not any(k in last_assistant for k in _CORRECTION_KEYWORDS):
        return None
    # 逆質問（区切り以降）は訂正ではないので外す。
    text = last_assistant.split(GATE_SEPARATOR, 1)[0]
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s and s.strip()]
    hits = [s for s in sentences if any(k in s for k in _CORRECTION_KEYWORDS)]
    if not hits:
        return None
    fact = " ".join(hits)
    if len(fact) > _CORRECTION_MAX_CHARS:
        fact = fact[:_CORRECTION_MAX_CHARS].rstrip() + "…"
    return fact
