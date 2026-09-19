"""非LLM 一次判定 ``prejudge``（Phase 1 入口統合 §4.2 の [2] 段）。

正本設計書: ``docs/features/learning_chat_entry_unification_design.md``（LC5）。

「明らかに教材内容の問い」だけを ``"DOMAIN_RAG"`` として先に確定させ、意図分類の
LLM コールを省く（**どの経路でも LLM 回数を現行より増やさない**の実現手段）。
決められないときは必ず ``None`` を返して既存の :func:`_classify_intent` へ落とす —
**縮退はこの1本だけ**。

規律:

* **純関数**（FastAPI / sqlalchemy / core.llm を import しない・I/O なし・
  グローバル状態なし・同一入力に同一出力）。
* **分野語をコードに書かない**（開発ルール7）。分野固有の内容語は呼び出し側が
  ``content_terms`` として渡す（``learning.py`` の ``_CONTENT_QUESTION_TERMS`` +
  ``_cartridge_content_terms(cartridge_id)``。``cartridge_id`` が空なら後者を
  呼ばない規律は呼び出し側に残る）。
* **「casual らしさ」を判定しない**（設計 §4.2）。軽口をキーワードで検出すると
  誤爆が人格評価に見える。判定するのは「教材内容の問いか」だけで、そうでない
  ことの判断（= CHIT_CHAT / LEARNING_ADVICE / USAGE_HELP）は LLM 分類に残す。
* **学習相談・使い方の合図があれば必ず ``None``**。一次判定が
  ``LEARNING_ADVICE`` / ``USAGE_HELP`` の受け皿を奪わない（rag-chat §2.9 の
  判定順を実質的に崩さないための保守ガード）。
* **履歴・過去の様相・学習者モデルを入力にしない**（LC4）。入力は当該発話のみ。
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["prejudge"]


#: 学習の進め方についてのメタ質問の合図。1つでも含まれたら一次判定は降りる
#: （``LEARNING_ADVICE`` ルートを奪わない）。分野非依存語のみ。
_ADVICE_CUES: tuple[str, ...] = (
    "進め方", "どう進め", "どうやって進め", "何から", "どこから始め", "どこから学",
    "学習計画", "勉強の計画", "学習の順番", "順番", "スケジュール",
    "next に何", "次に何を", "何を学べ", "おすすめの学び方", "勉強法", "学習方法",
)

#: 画面・システムの使い方の合図。1つでも含まれたら一次判定は降りる
#: （``USAGE_HELP`` ルートを奪わない）。``learning.py`` 側の HELP pre-route は
#: 「参照語 × 問い形」の共起で判定するが、こちらは**より保守的に参照語だけ**で降りる。
_HELP_CUES: tuple[str, ...] = (
    "画面", "ボタン", "操作", "アプリ", "この機能", "音声モード", "音声入力",
    "マイク", "ヘルプ", "メニュー", "使い方",
)

#: 問い形（日本語の疑問表現）。内容語1つだけのときの補強シグナルに使う。
_QUESTION_FORMS: tuple[str, ...] = (
    "?", "？", "ですか", "でしょうか", "教えて", "説明して", "とは", "なぜ", "なんで",
    "どうして", "どういう", "どのよう", "違い", "意味", "わからな", "分からな",
)

#: LaTeX の代表的なコマンド（数式らしさの判定に使う。網羅ではなく代表のみ）。
_MATH_COMMANDS: tuple[str, ...] = (
    "\\frac", "\\int", "\\sum", "\\sqrt", "\\partial", "\\alpha", "\\beta",
    "\\gamma", "\\Delta", "\\nabla", "\\infty", "\\cdot", "\\times", "\\left",
)

#: 数式らしい記号（単独では弱いので、`=` の両隣に英数字がある形だけを採る）。
_MATH_OPERATORS: tuple[str, ...] = ("=", "≒", "∝", "∫", "∂", "Σ", "√")

#: 内容語1つ + 問い形のときに要求する最小長（短すぎる断片は LLM 分類へ落とす）。
_MIN_SUBSTANTIVE_LENGTH = 12

#: 内容語の共起（= 別々の内容語がこの数以上）だけで教材内容の問いとみなす閾値。
_MIN_CONTENT_TERM_COOCCURRENCE = 2


def _contains_any(text: str, lowered: str, terms: Sequence[str]) -> bool:
    for term in terms:
        term = str(term or "")
        if not term:
            continue
        if term in text or term.lower() in lowered:
            return True
    return False


def _count_content_terms(text: str, lowered: str, terms: Sequence[str]) -> int:
    """共起の判定に使う「相異なる内容語」の数（同じ語の再出現は数えない）。"""
    seen: set[str] = set()
    for term in terms:
        term = str(term or "").strip()
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        if term in text or key in lowered:
            seen.add(key)
    return len(seen)


def _has_math_notation(text: str) -> bool:
    """``$…$`` / LaTeX コマンド / 記号つきの等式を含むか（決定論・保守的）。"""
    if text.count("$") >= 2:
        return True
    if any(cmd in text for cmd in _MATH_COMMANDS):
        return True
    # `=` は日本語の平文にも現れうるので、両隣に英数字がある「式らしい」形だけ採る。
    for idx, ch in enumerate(text):
        if ch not in _MATH_OPERATORS:
            continue
        if ch != "=":
            return True
        left = text[idx - 1] if idx > 0 else ""
        right = text[idx + 1] if idx + 1 < len(text) else ""
        if left.isalnum() and right.isalnum():
            return True
    return False


def prejudge(message: str, *, content_terms: Sequence[str] = ()) -> str | None:
    """「明らかに教材内容の問い」なら ``"DOMAIN_RAG"``、決められなければ ``None``。

    Parameters
    ----------
    message:
        当該発話（**これだけが入力**。履歴・過去の様相は渡さない = LC4）。
    content_terms:
        「教材内容らしさ」の語彙。呼び出し側が分野非依存語 + カートリッジ由来語を
        合成して渡す（分野語をこのモジュールに書かないための供給口）。

    Notes
    -----
    返り値は ``"DOMAIN_RAG"`` か ``None`` の2値のみ。**CHIT_CHAT / casual を
    ここで推定することはない**（設計 §4.2）。
    """
    text = (message or "").strip()
    if not text:
        return None

    lowered = text.lower()

    # 保守ガード: 学習相談・使い方の合図があれば一次判定は降りる（受け皿を奪わない）。
    if _contains_any(text, lowered, _ADVICE_CUES):
        return None
    if _contains_any(text, lowered, _HELP_CUES):
        return None

    content_hits = _count_content_terms(text, lowered, content_terms)
    has_question_form = _contains_any(text, lowered, _QUESTION_FORMS)
    has_math = _has_math_notation(text)

    # 規則A: 相異なる内容語の共起（分野語 or 学問一般語が2つ以上）。
    if content_hits >= _MIN_CONTENT_TERM_COOCCURRENCE:
        return "DOMAIN_RAG"
    # 規則B: 数式らしさ + （内容語 or 問い形）。
    if has_math and (content_hits >= 1 or has_question_form):
        return "DOMAIN_RAG"
    # 規則C: 内容語1つ + 問い形 + 十分な長さ（短い断片は LLM 分類へ落とす）。
    if content_hits >= 1 and has_question_form and len(text) >= _MIN_SUBSTANTIVE_LENGTH:
        return "DOMAIN_RAG"

    return None
