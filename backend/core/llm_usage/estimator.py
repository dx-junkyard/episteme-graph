"""決定論的トークン推計（設計書 §4）。

LLM を呼ばない・DB を読まない・乱数や時刻に依存しない。同じ入力には常に同じ値を返す
（テスト可能性・事前見積り両用）。

優先順位（§4.1）:
  1. reported          — プロバイダ報告値（この module の責務外。observe.py が扱う）
  2. estimated_tokenizer — tiktoken が import 可能かつエンコーディングが解決できる場合のみ
  3. estimated_heuristic — 文字種ベースのヒューリスティック（依存ゼロ・常に動く）

``tiktoken`` は optional import。新規必須依存にはしない。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

# --- 係数（すべてモジュールレベル定数。設計書 §4.2 / §4.3 の正本） ---

# ヒューリスティック式: tokens = ceil(cjk_chars * CJK_TOKENS_PER_CHAR
#                                     + other_chars / OTHER_CHARS_PER_TOKEN)
# 経験則: o200k 系トークナイザで日本語は約 1〜1.5 文字/トークン、英語は約 4 文字/トークン。
# 係数は安全側（過大方向）に丸めてある。
CJK_TOKENS_PER_CHAR = 1.0
OTHER_CHARS_PER_TOKEN = 4.0

# メッセージリストの框（1メッセージあたりのオーバーヘッド + 応答開始の priming）。
PER_MESSAGE_OVERHEAD = 4
PRIMING_TOKENS = 3

# vision: 寸法不明な画像1枚あたりの定数トークン数（= 85 + 4*170。detail=high の典型値）。
FLAT_IMAGE_TOKENS = 765

# 推計値のレンジ幅（±40%）。ヒューリスティックはもちろん、tokenizer 使用時も
# 罫線・不可視トークン等の不確実性が残るため同じ幅を適用する（点推定を見せない。U5）。
ESTIMATE_MARGIN = 0.4

# tiktoken 使用時のフォールバックエンコーディング（GPT-4o 系以降の既定）。
_DEFAULT_TIKTOKEN_ENCODING = "o200k_base"

# --- CJK 判定レンジ（ひらがな・カタカナ・CJK統合漢字・全角記号） ---
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3000, 0x303F),  # CJK の記号及び句読点（全角スペース含む）
    (0x3040, 0x309F),  # ひらがな
    (0x30A0, 0x30FF),  # カタカナ
    (0x3400, 0x4DBF),  # CJK統合漢字拡張A
    (0x4E00, 0x9FFF),  # CJK統合漢字
    (0xF900, 0xFAFF),  # CJK互換漢字
    (0xFF00, 0xFFEF),  # 半角・全角形（全角記号・全角英数）
)


@dataclass
class TokenEstimate:
    """推計結果。点推定 (`tokens`) とレンジ (`low`/`high`) を常に併せ持つ。"""

    tokens: int
    low: int
    high: int
    usage_source: str


def _is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def _heuristic_token_count(text: str) -> int:
    if not text:
        return 0
    cjk_chars = sum(1 for ch in text if _is_cjk_char(ch))
    other_chars = len(text) - cjk_chars
    return math.ceil(cjk_chars * CJK_TOKENS_PER_CHAR + other_chars / OTHER_CHARS_PER_TOKEN)


def _try_tiktoken_count(text: str, model: str | None) -> int | None:
    """tiktoken が使えれば実トークン数を返す。使えない/失敗する場合は None。"""
    try:
        import tiktoken  # type: ignore
    except ImportError:
        return None

    try:
        encoding = None
        if model:
            try:
                encoding = tiktoken.encoding_for_model(model)
            except KeyError:
                encoding = None
        if encoding is None:
            encoding = tiktoken.get_encoding(_DEFAULT_TIKTOKEN_ENCODING)
        return len(encoding.encode(text))
    except Exception:
        return None


def _build_estimate(tokens: int, usage_source: str) -> TokenEstimate:
    tokens = max(0, int(tokens))
    low = math.floor(tokens * (1 - ESTIMATE_MARGIN))
    high = math.ceil(tokens * (1 + ESTIMATE_MARGIN))
    return TokenEstimate(tokens=tokens, low=low, high=high, usage_source=usage_source)


def estimate_text_tokens(text: str, *, model: str | None = None) -> TokenEstimate:
    """1本のテキストのトークン数を推計する。"""
    text = text or ""
    tokenizer_count = _try_tiktoken_count(text, model)
    if tokenizer_count is not None:
        return _build_estimate(tokenizer_count, "estimated_tokenizer")
    return _build_estimate(_heuristic_token_count(text), "estimated_heuristic")


def _extract_text_from_content(content) -> str:
    """メッセージの content からテキスト部分だけを取り出す。

    vision 用の list 形式（``[{"type": "text", "text": "..."}, {"type": "image_url", ...}]``）
    が来た場合は image パートを無視し、text パートだけを連結する。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    texts.append(str(part.get("text", "")))
                # image_url / image 等の非テキストパートは意図的に無視する
            elif isinstance(part, str):
                texts.append(part)
        return "\n".join(texts)
    return str(content)


def estimate_messages_tokens(
    messages: list[dict], *, model: str | None = None, schema: dict | None = None
) -> TokenEstimate:
    """メッセージリスト（chat / structured output）のトークン数を推計する。

    ``Σ tokens(content) + PER_MESSAGE_OVERHEAD * len(messages) + PRIMING_TOKENS``。
    ``schema`` 指定時（structured output）は ``json.dumps(schema)`` のトークンも加算する
    （実課金対象のため。設計書 §4.2）。
    """
    messages = messages or []
    total_tokens = 0
    usage_source = "estimated_heuristic"

    for message in messages:
        content = message.get("content", "") if isinstance(message, dict) else ""
        text = _extract_text_from_content(content)
        estimate = estimate_text_tokens(text, model=model)
        total_tokens += estimate.tokens
        usage_source = estimate.usage_source

    total_tokens += PER_MESSAGE_OVERHEAD * len(messages) + PRIMING_TOKENS

    if schema:
        schema_text = json.dumps(schema, ensure_ascii=False)
        estimate = estimate_text_tokens(schema_text, model=model)
        total_tokens += estimate.tokens
        usage_source = estimate.usage_source

    return _build_estimate(total_tokens, usage_source)


def _openai_image_tokens(width: float, height: float) -> int:
    """OpenAI vision の公式式: 長辺2048px・短辺768pxに縮小後、512pxタイル数から算出する。

    tokens = 85 + 170 * tiles
    """
    if width <= 0 or height <= 0:
        return FLAT_IMAGE_TOKENS

    long_side = max(width, height)
    if long_side > 2048:
        scale = 2048 / long_side
        width *= scale
        height *= scale

    short_side = min(width, height)
    if short_side > 768:
        scale = 768 / short_side
        width *= scale
        height *= scale

    tiles_w = math.ceil(width / 512)
    tiles_h = math.ceil(height / 512)
    tiles = max(1, tiles_w * tiles_h)
    return 85 + 170 * tiles


def estimate_vision_tokens(image_dims: list[tuple[int, int] | None]) -> TokenEstimate:
    """画像トークンを推計する（設計書 §4.3）。

    寸法既知（``(width, height)``）なら OpenAI の公式式、不明（``None``）なら
    定数 ``FLAT_IMAGE_TOKENS`` トークン/枚で推計する。usage_source は常に
    ``estimated_heuristic``（tiktoken のテキストトークナイザは使わないため）。
    """
    total = 0
    for dims in image_dims or []:
        if dims is None:
            total += FLAT_IMAGE_TOKENS
        else:
            width, height = dims
            total += _openai_image_tokens(width, height)
    return _build_estimate(total, "estimated_heuristic")
