"""Episteme Graph — インタラクティブ・レクチャーモード コアロジック (Issue #66)。

チャンクテキストから音声読み上げ用テキスト・数式メタデータを生成し、
コーストピックに紐づくチャンクのレクチャーシーケンスを構築する。
"""

from __future__ import annotations

import json
import logging
import re

from core.llm import generate_text, get_llm_params

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ワードタイムスタンプ推定
# ---------------------------------------------------------------------------


def estimate_word_timestamps(text: str, total_duration_ms: int) -> list[dict]:
    """テキストの文字位置に基づいてワードタイムスタンプを近似的に生成する。"""
    words = re.findall(r'\S+', text)
    if not words:
        return []

    total_chars = sum(len(w) for w in words)
    if total_chars == 0:
        return []

    timestamps = []
    current_ms = 0
    for word in words:
        word_duration = int(total_duration_ms * len(word) / total_chars)
        timestamps.append({
            "word": word,
            "start_ms": current_ms,
            "end_ms": current_ms + word_duration,
        })
        current_ms += word_duration

    return timestamps

# ---------------------------------------------------------------------------
# spoken_text / formulas 生成
# ---------------------------------------------------------------------------

_SPOKEN_TEXT_PROMPT = """あなたは学術テキストを音声読み上げ用に変換するアシスタントです。

以下のチャンクテキストを処理してください:
1. **spoken_text**: 音声で読み上げるためのテキストを生成してください。
   - LaTeX 数式は自然言語に変換する（例: `$E = mc^2$` → 「Eイコールmcの二乗」）
   - 専門用語にはふりがなや読み方を含める
   - 段落の区切りは自然な「間」を表す「...」を入れる
   - 参照番号や図表番号は省略してよい
2. **formulas**: テキスト中に出現するすべての数式をリストアップしてください。各数式は:
   - `id`: "formula_0", "formula_1", ... の連番
   - `latex`: 元の LaTeX 表記
   - `spoken`: 音声読み上げ用のテキスト

## チャンクテキスト:
{chunk_text}

## 出力形式 (厳密にJSON):
{{
  "spoken_text": "...",
  "formulas": [
    {{"id": "formula_0", "latex": "E = mc^2", "spoken": "Eイコールmcの二乗"}}
  ]
}}

重要: JSON のみを出力してください。マークダウンコードフェンスは不要です。"""


def generate_spoken_text_and_formulas(chunk_text: str) -> dict:
    """チャンクテキストから spoken_text と formulas を LLM で生成する。

    Returns
    -------
    dict
        ``{"spoken_text": str, "formulas": list[dict]}``
    """
    if not chunk_text or not chunk_text.strip():
        return {"spoken_text": "", "formulas": []}

    params = get_llm_params("fast")
    prompt = _SPOKEN_TEXT_PROMPT.format(chunk_text=chunk_text[:4000])

    try:
        raw = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
        )
        cleaned = raw.strip()
        # Strip markdown fences if present
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            cleaned = "\n".join(lines)
        result = json.loads(cleaned)
        return {
            "spoken_text": result.get("spoken_text", ""),
            "formulas": result.get("formulas", []),
        }
    except Exception:
        logger.warning("spoken_text generation failed, using fallback", exc_info=True)
        return _fallback_spoken_text(chunk_text)


def _fallback_spoken_text(chunk_text: str) -> dict:
    """LLM が失敗した場合のフォールバック: 数式を簡易的に置換する。"""
    formulas = []
    spoken = chunk_text

    # Display math $$...$$
    def _replace_display(m):
        idx = len(formulas)
        latex = m.group(1).strip()
        formulas.append({"id": f"formula_{idx}", "latex": latex, "spoken": f"（数式{idx + 1}）"})
        return f"（数式{idx + 1}）"

    spoken = re.sub(r"\$\$([\s\S]+?)\$\$", _replace_display, spoken)

    # Inline math $...$
    def _replace_inline(m):
        idx = len(formulas)
        latex = m.group(1).strip()
        formulas.append({"id": f"formula_{idx}", "latex": latex, "spoken": f"（数式{idx + 1}）"})
        return f"（数式{idx + 1}）"

    spoken = re.sub(r"\$([^\$\n]+?)\$", _replace_inline, spoken)

    return {"spoken_text": spoken, "formulas": formulas}


# ---------------------------------------------------------------------------
# レクチャーシーケンス構築
# ---------------------------------------------------------------------------


def build_lecture_sequence(
    topic_id: str,
    course_data: dict,
    chunks: list[dict],
) -> list[dict]:
    """トピックに紐づくチャンクを学習順序に並べてレクチャーシーケンスを構築する。

    Parameters
    ----------
    topic_id : str
        対象トピックID
    course_data : dict
        コースの JSONB データ (topics, chapters, concepts など)
    chunks : list[dict]
        トピックに関連するチャンクのリスト

    Returns
    -------
    list[dict]
        セグメントのリスト (chunk_id, chunk_index, text, spoken_text, formulas, ...)
    """
    if not chunks:
        return []

    # トピックの前提知識情報を取得
    topic_info = None
    for t in course_data.get("topics", []):
        if t.get("id") == topic_id:
            topic_info = t
            break

    # chunk_index でソート（トポロジカルソートの簡易版）
    sorted_chunks = sorted(chunks, key=lambda c: c.get("chunk_index", 0))

    segments = []
    for chunk in sorted_chunks:
        spoken = chunk.get("spoken_text") or chunk.get("text", "")
        formulas = chunk.get("formulas") or []

        segments.append({
            "chunk_id": str(chunk.get("id", "")),
            "chunk_index": chunk.get("chunk_index", 0),
            "text": chunk.get("text", ""),
            "spoken_text": spoken,
            "formulas": formulas,
            "has_audio": chunk.get("has_audio", False),
            "duration_ms": chunk.get("duration_ms", 0),
        })

    return segments
