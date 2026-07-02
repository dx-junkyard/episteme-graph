"""Stage 0 — TensionPrefilter（非LLM・同期・数ms）。

learning.py の record_interest_trace(...) 呼び出し直前でヒント判定し
payload に tension_hint を付けるだけ。false negative / false positive とも許容
（後段の LLM が弁別する）。目的は LLM コスト削減のゲートであり精度は求めない。
判定は best-effort: 例外はログのみで握りつぶし、チャット応答を止めない。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ヘッジ・逆接・前提照会マーカー（部分一致）
_HEDGE_MARKERS = (
    "なんとなく", "気がする", "気持ち悪い", "引っかか", "腑に落ち",
    "しっくりこ", "違和感", "本当に", "ほんとうに", "どうしても",
    "でも", "けど", "だとしたら", "矛盾", "おかしくない", "変じゃない",
    "似てる気が", "前提", "そもそも",
)
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


def _has_revisit(text: str, recent_user_texts: list[str], min_len: int = _REVISIT_MIN_LEN) -> bool:
    """直近の学習者発話との同語再訪（4文字以上の内容語的な共通部分文字列）を検出する。"""
    if not text or not recent_user_texts:
        return False
    for i in range(len(text) - min_len + 1):
        sub = text[i:i + min_len]
        if not _is_content_like(sub):
            continue
        if any(sub in prev for prev in recent_user_texts if prev):
            return True
    return False


def judge_tension_hint(user_text: str, recent_user_texts: list[str]) -> bool:
    """ヘッジ/逆接マーカー、または直近3往復内の同語再訪でヒントを立てる（純関数）。

    recent_user_texts には直近の学習者発話（新しい順・古い順いずれも可）を最大3件程度渡す。
    例外時は False（チャット応答を止めない）。
    """
    try:
        t = user_text or ""
        if any(m in t for m in _HEDGE_MARKERS):
            return True
        # 納得表明で閉じた発話は、再訪の字面一致だけでヒントを立てない
        if any(m in t for m in _COUNTER_MARKERS):
            return False
        return _has_revisit(t, list(recent_user_texts or [])[-3:])
    except Exception as exc:  # best-effort（P6）
        logger.warning("judge_tension_hint failed: %s", exc)
        return False
