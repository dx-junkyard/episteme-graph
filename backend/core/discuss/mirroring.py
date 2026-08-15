"""鏡面化 move（EX-3b 裁定 / 案B-lite）のサーバ側決定論抽出（非LLM・純関数）。

正本: docs/features/seminar_brief_mirroring_design.md §2 / §3 精査記録①③。

discuss の言い直し（revoice）・詰まりの足場かけで、LLM が鏡文を固定マーカー
``〔鏡〕…〔/鏡〕`` に出す（プロンプト契約は ``_get_discuss_system_prompt``）。本モジュールは
その本文中マーカーをサーバ側で決定論的に構造化フィールドへ正規化する
（``extract_inline_actions`` の一元化方針と同じ規律 — フロントに regex を書かせない）。

原則:
  - **verbatim 検査（EX-3b ②）**: 鏡文中の「」内テキストのいずれかが学習者の直前発話の
    逐語部分文字列（前後空白の正規化のみ許容）でなければ、鏡扱いせずマーカーだけ剥がして
    本文に残す（縮退・再生成なし, P6）。鏡が映してよいのは発話であって能力・傾向ではない。
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


def _has_verbatim_quote(mirror_text: str, learner_message: str) -> bool:
    """鏡文中の「」内テキストのいずれかが学習者発話の逐語部分文字列かを検査する。

    許容する正規化は引用側の**前後空白**の除去のみ（内部の言い換え・要約は不合格）。
    引用が1つも無い鏡文は「逐語引用ゼロの純合成」なので不合格（EX-3b ②）。
    """
    learner_message = str(learner_message or "")
    if not learner_message:
        return False
    for quote in _QUOTE_RE.findall(mirror_text or ""):
        quote = quote.strip()
        if quote and quote in learner_message:
            return True
    return False


def _strip_markers(text: str) -> str:
    """残存するマーカーだけを剥がし、中身のテキストは本文に残す（情報を落とさない）。"""
    return text.replace("〔鏡〕", "").replace("〔/鏡〕", "")


def extract_mirror(answer: str, learner_message: str) -> tuple[str, dict | None]:
    """回答本文から鏡文を決定論抽出する（最初の1個のみ）。

    Returns:
        ``(clean_answer, mirror)``。鏡が有効なら ``mirror = {"text": 鏡文}`` で本文から
        当該スパンを除去。マーカーが無い・verbatim 検査に不合格なら ``mirror = None`` で、
        後者はマーカーだけ剥がして中身を本文に残す（縮退・再生成なし）。
        2個目以降のマーカーは常にマーカーのみ剥がして本文へ残す
        （生マーカーを学習者に見せない — 構造化されるのは最初の1個だけ）。
    """
    answer = str(answer or "")
    match = _MIRROR_RE.search(answer)
    if match is None:
        return answer, None

    mirror_text = match.group(1).strip()
    if not _has_verbatim_quote(mirror_text, learner_message):
        # verbatim 不合格: 鏡扱いせずマーカーだけ剥がして本文に残す（P4/P6）。
        return _strip_markers(answer), None

    clean_answer = (answer[: match.start()] + answer[match.end():]).strip()
    # 2個目以降（LLM が契約に反して複数出した場合）はマーカーのみ除去して本文に残す。
    clean_answer = _strip_markers(clean_answer)
    return clean_answer, {"text": mirror_text}
