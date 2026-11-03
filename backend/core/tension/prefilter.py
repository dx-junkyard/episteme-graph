"""Stage 0 — TensionPrefilter（非LLM・同期・数ms）。

learning.py の record_interest_trace(...) 呼び出し直前でヒント判定し
payload に tension_hint を付けるだけ。false negative / false positive とも許容
（後段の LLM が弁別する）。目的は LLM コスト削減のゲートであり精度は求めない。
判定は best-effort: 例外はログのみで握りつぶし、チャット応答を止めない。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ヘッジ・逆接・前提照会マーカー（部分一致）
_HEDGE_MARKERS = (
    "なんとなく", "気がする", "気持ち悪い", "引っかか", "腑に落ち",
    "しっくりこ", "違和感", "本当に", "ほんとうに", "どうしても",
    "けど", "だとしたら", "矛盾", "おかしくない", "変じゃない",
    "おかしい", "納得できな", "納得がいかな", "なのに",
    "似てる気が", "前提", "そもそも",
)
# 「でも」は逆接のときだけ（「何でも」「いつでも」「どこでも」「誰でも」の副助詞を拾わない）。
_DEMO_RE = re.compile(r"(?<![何つこ誰])でも")

# 英語の逆接・矛盾・疑いのマーカー（語境界・casefold 済みの本文に当てる。IK-0392）。
# 分野語は含めない。「理解した上での引っかかり」を示す言い回しだけを並べ、
# 「わからない」「説明して」のような理解の欠落（gap）は含めない（後段 LLM の弁別に
# 回す前に、ゲートを素通りさせない）。
_EN_MARKER_RE = re.compile(
    r"\b(?:"
    r"but|however|although|though|whereas"
    r"|contradict\w*|inconsisten\w*|conflict\w*|paradox\w*|at odds"
    r"|doesn't that|isn't that|aren't (?:these|those|they)|wouldn't that|shouldn't it"
    r"|how can (?:it|that|this|they)|then why|why would"
    r"|doesn't (?:make sense|add up|fit)|not convinced|hard to believe|i doubt"
    r"|feels? (?:off|wrong|strange)|seems? (?:off|wrong|strange|odd)"
    r"|assum\w*|premise\w*"
    r")\b"
)

# 理解の欠落（gap）・依頼の定型。マーカーが無い発話でこれに当たれば、同語再訪の
# 字面一致だけではヒントを立てない（「DCF法とは何ですか」「確認問題を出してください」
# 「What does X mean?」は引っかかりではない。IK-0392）。
_GAP_OR_REQUEST_RE = re.compile(
    r"(?:とは(?:何|なん|どういう)|って(?:何|なん)|とは[？?]|の意味"
    r"|(?:を|について)(?:教えて|説明して|出して|解説して)|ください|くださいませ|お願いします)"
    r"|\b(?:what (?:is|are|does|do)|what's|could you|can you|would you|please"
    r"|explain|tell me|give me|i don't know|i do not know|define|meaning of)\b"
)

# 再訪判定で「内容語」と見なさない英語の機能語（4文字以上のものだけ並べれば足りる）。
_EN_STOPWORDS = frozenset(
    {
        "what", "which", "where", "when", "does", "this", "that", "these", "those",
        "there", "their", "they", "them", "then", "than", "with", "from", "into",
        "about", "have", "been", "were", "will", "would", "could", "should", "your",
        "here", "just", "like", "only", "also", "very", "more", "most", "some",
        "such", "each", "other", "paper", "please", "thank", "thanks", "explain",
        "mean", "means", "because", "think", "know", "understand", "question",
        "answer", "words", "simply", "main", "result", "results", "still",
        "doesn", "isn", "aren", "wasn", "weren", "didn", "hello", "sorry",
    }
)
_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z-]{2,}[A-Za-z]")

# 納得クローズのマーカー（ヘッジ無しの再訪判定を抑制する）
_COUNTER_MARKERS = ("わかりました", "なるほど", "理解できました", "納得")

# 再訪判定に使う共通部分文字列の最小長（形態素解析なしの素朴判定）
_REVISIT_MIN_LEN = 4


def _is_content_like(sub: str) -> bool:
    """助詞・言い回しだけの一致を弾く: 非ひらがな（漢字・カタカナ・英数）を2字以上含むこと。"""
    content = 0
    for ch in sub:
        if ch.isspace() or not ch.isalnum():
            return False
        if not ("ぁ" <= ch <= "ゟ"):  # ひらがな以外
            content += 1
    return content >= 2


def _latin_content_words(text: str) -> set[str]:
    return {
        w.casefold() for w in _LATIN_WORD_RE.findall(text or "")
        if w.casefold() not in _EN_STOPWORDS
    }


def _has_revisit(text: str, recent_user_texts: list[str], min_len: int = _REVISIT_MIN_LEN) -> bool:
    """直近の学習者発話との同語再訪（4文字以上の内容語的な共通部分文字列）を検出する。

    英字は**語単位**で照合する（IK-0392: 以前は ``What`` / ``does`` / ``this`` の4文字
    断片が毎回一致し、英語の会話ではほぼ全発話にヒントが立っていた）。
    日本語など空白で区切らない文字は従来どおり4文字窓で照合する。
    """
    if not text or not recent_user_texts:
        return False
    previous = [prev for prev in recent_user_texts if prev]
    words = _latin_content_words(text)
    if words and any(words & _latin_content_words(prev) for prev in previous):
        return True
    for i in range(len(text) - min_len + 1):
        sub = text[i:i + min_len]
        if sub.isascii():
            continue  # 英字・数字だけの窓は上の語単位照合に任せる
        if not _is_content_like(sub):
            continue
        if any(sub in prev for prev in previous):
            return True
    return False


def _has_marker(text: str) -> bool:
    if any(m in text for m in _HEDGE_MARKERS):
        return True
    if _DEMO_RE.search(text):
        return True
    return bool(_EN_MARKER_RE.search(_normalize_en(text)))


_THANKS_MARKERS = ("ありがとう", "助かりました", "お疲れさま", "お疲れ様")
_THANKS_EN_RE = re.compile(r"\b(?:thank you|thanks)\b")


def _is_thanks_without_question(text: str) -> bool:
    """お礼で始まる・お礼を含み、問いの形（？ / ?）を持たない発話か（IK-0474）。"""
    body = text or ""
    if "?" in body or "？" in body:
        return False
    return any(m in body for m in _THANKS_MARKERS) or bool(_THANKS_EN_RE.search(_normalize_en(body)))


def _normalize_en(text: str) -> str:
    return (text or "").replace("’", "'").replace("‘", "'").casefold()


def judge_tension_hint(user_text: str, recent_user_texts: list[str]) -> bool:
    """ヘッジ/逆接マーカー、または直近3往復内の同語再訪でヒントを立てる（純関数）。

    判定順: マーカー（日英）→ 納得表明なら偽 → 定義質問・依頼の定型なら偽 → 同語再訪。

    recent_user_texts には直近の学習者発話（新しい順・古い順いずれも可）を最大3件程度渡す。
    例外時は False（チャット応答を止めない）。
    """
    try:
        t = user_text or ""
        # IK-0474: お礼・締めくくりの発話（問いを含まない）は引っかかりの手がかりにしない
        # （「ありがとうございました。ゼミではこの前提のことを話してみます。」が「前提」の
        # マーカーでヒント付きになっていた）。
        if _is_thanks_without_question(t):
            return False
        if _has_marker(t):
            return True
        # 納得表明で閉じた発話は、再訪の字面一致だけでヒントを立てない
        if any(m in t for m in _COUNTER_MARKERS):
            return False
        # 定義を尋ねる・依頼する定型は理解の欠落（gap）で、引っかかりではない（IK-0392）
        if _GAP_OR_REQUEST_RE.search(_normalize_en(t)):
            return False
        return _has_revisit(t, list(recent_user_texts or [])[-3:])
    except Exception as exc:  # best-effort（P6）
        logger.warning("judge_tension_hint failed: %s", exc)
        return False
