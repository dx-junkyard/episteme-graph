"""鏡面化 move（EX-3b 裁定 / 案B-lite）のサーバ側決定論抽出（非LLM・純関数）。

正本: docs/features/seminar_brief_mirroring_design.md §2 / §3 精査記録①③。

discuss の言い直し（revoice）・詰まりの足場かけで、LLM が鏡文を固定マーカー
``〔鏡〕…〔/鏡〕`` に出す（プロンプト契約は ``_get_discuss_system_prompt``）。本モジュールは
その本文中マーカーをサーバ側で決定論的に構造化フィールドへ正規化する
（``extract_inline_actions`` の一元化方針と同じ規律 — フロントに regex を書かせない）。

原則:
  - **verbatim 検査（EX-3b ②）**: 鏡文中の「」内テキストの**すべて**が学習者の直前発話の
    逐語部分文字列（前後空白の正規化のみ許容・各引用2文字以上）でなければ、鏡扱いせず
    マーカーだけ剥がして本文に残す（縮退・再生成なし, P6）。1つでも捏造引用が混ざった鏡は
    全体を鏡扱いしない。鏡が映してよいのは発話であって能力・傾向ではない。
  - **窓の外へ持ち出さない（EX-3b ④）**: 抽出した鏡文の保存・プロファイル化・別機能への
    還流は行わない（呼び出し側の責務も含めガードレールで固定）。会話履歴 JSONB に
    生 answer がマーカー込みで残るのは既存挙動のままで、これは window_history の
    窓内再注入（全応答共通の構造）であり禁止対象外（§3 精査③）。
  - FastAPI / LLM を import しない（テスタビリティ確保・同期パスの純関数のみ）。
"""

from __future__ import annotations

import re

# 鏡文の固定マーカー（DOTALL: 鏡文が改行を含んでも1個の鏡として扱う）
_MIRROR_RE = re.compile(r"〔鏡〕(.+?)〔/鏡〕", re.DOTALL)
# 鏡文中の逐語引用スパン（「」内。入れ子は想定しない — 契約プロンプトが単純引用を指示する）
_QUOTE_RE = re.compile(r"「([^「」]+)」")

# 逐語証拠として認める引用の最短長（1文字の一致は偶然一致しやすく証拠として弱すぎる）
_MIN_QUOTE_CHARS = 2


def _has_verbatim_quote(mirror_text: str, learner_message: str) -> bool:
    """鏡文中の「」内テキストの**すべて**が学習者発話の逐語部分文字列かを検査する。

    all-quotes 判定: 1つでも捏造（非逐語）引用が混ざった鏡文は不合格にする
    （any-quote だと逐語引用1つを添えれば残りを自由に捏造できてしまう）。
    許容する正規化は引用側の**前後空白**の除去のみ（内部の言い換え・要約は不合格）。
    引用が1つも無い鏡文は「逐語引用ゼロの純合成」なので不合格（EX-3b ②）。
    1文字だけの引用は逐語証拠として弱すぎるため不合格（``_MIN_QUOTE_CHARS``）。
    """
    learner_message = str(learner_message or "")
    if not learner_message:
        return False
    quotes = [q.strip() for q in _QUOTE_RE.findall(mirror_text or "")]
    if not quotes:
        return False
    return all(len(q) >= _MIN_QUOTE_CHARS and q in learner_message for q in quotes)


def _strip_markers(text: str) -> str:
    """残存するマーカーだけを剥がし、中身のテキストは本文に残す（情報を落とさない）。"""
    return text.replace("〔鏡〕", "").replace("〔/鏡〕", "")


def extract_mirror(answer: str, learner_message: str) -> tuple[str, dict | None]:
    """回答本文から鏡文を決定論抽出する（最初の1個のみ）。

    Returns:
        ``(clean_answer, mirror)``。鏡が有効なら ``mirror = {"text": 鏡文}`` で本文から
        当該スパンを除去。マーカーが無い・verbatim 検査に不合格なら ``mirror = None`` で、
        後者はマーカーだけ剥がして中身を本文に残す（縮退・再生成なし）。
        閉じマーカー欠落等でペアが成立しない場合も、マーカー断片だけは剥がして返す
        （生マーカーを学習者に見せない）。2個目以降のマーカーは常にマーカーのみ剥がして
        本文へ残す（構造化されるのは最初の1個だけ）。ネストした残存マーカーは
        鏡文側からも剥がす。
    """
    answer = str(answer or "")
    match = _MIRROR_RE.search(answer)
    if match is None:
        # ペア不成立（閉じ/開きマーカー欠落）でも生マーカーの断片は学習者に見せない。
        if "〔鏡〕" in answer or "〔/鏡〕" in answer:
            return _strip_markers(answer), None
        return answer, None

    # ネスト等で group(1) にマーカー断片が残っても鏡文へ混入させない。
    mirror_text = _strip_markers(match.group(1)).strip()
    if not _has_verbatim_quote(mirror_text, learner_message):
        # verbatim 不合格: 鏡扱いせずマーカーだけ剥がして本文に残す（P4/P6）。
        return _strip_markers(answer), None

    clean_answer = (answer[: match.start()] + answer[match.end():]).strip()
    # 2個目以降（LLM が契約に反して複数出した場合）はマーカーのみ除去して本文に残す。
    clean_answer = _strip_markers(clean_answer)
    return clean_answer, {"text": mirror_text}
