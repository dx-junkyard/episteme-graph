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
    mastered_concepts: set[str] | None = None,
) -> list[dict]:
    """トピックに紐づくチャンクを学習順序に並べてレクチャーシーケンスを構築する。

    受講者の習得済み概念 (``mastered_concepts``) を考慮し、
    既知の基礎概念に相当するセグメントをスキップまたは簡易版に変換する。

    Parameters
    ----------
    topic_id : str
        対象トピックID
    course_data : dict
        コースの JSONB データ (topics, chapters, concepts など)
    chunks : list[dict]
        トピックに関連するチャンクのリスト
    mastered_concepts : set[str] | None
        受講者が習得済みの概念名の集合。None の場合はスキップ判定を行わない。

    Returns
    -------
    list[dict]
        セグメントのリスト (chunk_id, chunk_index, text, spoken_text, formulas, ...)
    """
    if not chunks:
        return []

    mastered = mastered_concepts or set()
    mastered_lower = {c.lower() for c in mastered} if mastered else set()

    # トピックの前提知識情報を取得
    topic_info = None
    for t in course_data.get("topics", []):
        if t.get("id") == topic_id:
            topic_info = t
            break

    # 前提知識の概念名リストを収集（習得済みかどうかの判定に使う）
    prerequisite_names: set[str] = set()
    if topic_info:
        for p in topic_info.get("prerequisites", []):
            name = p.get("name", p) if isinstance(p, dict) else str(p)
            if name:
                prerequisite_names.add(name.lower())

    # chunk_index でソート（トポロジカルソートの基盤）
    sorted_chunks = sorted(chunks, key=lambda c: c.get("chunk_index", 0))

    segments = []
    for chunk in sorted_chunks:
        spoken = chunk.get("spoken_text") or chunk.get("text", "")
        formulas = chunk.get("formulas") or []
        text = chunk.get("text", "")

        # 適応的スキップ判定: チャンクが習得済み前提知識のみに関わる場合
        segment_mode = _classify_segment(
            text, mastered_lower, prerequisite_names,
        )

        if segment_mode == "skip":
            # 完全スキップ: 習得済み前提知識のみのチャンクは除外
            continue
        elif segment_mode == "summary":
            # 簡易版: テキストを短い要約に置換
            spoken = f"（この部分は既に習得済みの内容です。要約: {text[:80]}…）"

        segments.append({
            "chunk_id": str(chunk.get("id", "")),
            "chunk_index": chunk.get("chunk_index", 0),
            "text": text,
            "spoken_text": spoken,
            "formulas": formulas,
            "has_audio": chunk.get("has_audio", False),
            "duration_ms": chunk.get("duration_ms", 0),
            "segment_mode": segment_mode,
        })

    return segments


def _classify_segment(
    text: str,
    mastered_lower: set[str],
    prerequisite_names: set[str],
) -> str:
    """チャンクテキストと習得状態から、セグメントの扱いを分類する。

    Returns
    -------
    str
        ``"full"`` — 通常表示
        ``"summary"`` — 簡易版 (習得済み前提概念の解説チャンク)
        ``"skip"`` — 完全スキップ (習得済み概念のみで構成される短い定義チャンク)
    """
    if not mastered_lower:
        return "full"

    text_lower = text.lower()
    text_len = len(text)

    # テキスト内に含まれる習得済み概念をカウント
    matched_mastered = 0
    matched_prereq = 0
    for concept in mastered_lower:
        if concept in text_lower:
            matched_mastered += 1
            if concept in prerequisite_names:
                matched_prereq += 1

    if matched_mastered == 0:
        return "full"

    # 短いチャンク（200文字以下）で習得済み概念のみ → スキップ
    if text_len <= 200 and matched_prereq > 0 and matched_mastered >= 1:
        return "skip"

    # やや長いチャンクで習得済み前提概念の説明が主 → 簡易版
    if matched_prereq >= 2 and text_len <= 500:
        return "summary"

    return "full"


# ---------------------------------------------------------------------------
# 習得済み概念の取得
# ---------------------------------------------------------------------------


def get_user_mastered_concepts(user_id: str, course_id: str, course_data: dict) -> set[str]:
    """受講者の習得済み概念名を収集する。

    1. コースデータ内の concepts で status="mastered" のもの
    2. PostgreSQL の learner_mastered_concepts テーブル
    3. チャット履歴が存在するトピックの概念 (学習済みとみなす)

    Returns
    -------
    set[str]
        習得済み概念名の集合（小文字正規化なし、原文のまま）
    """
    mastered: set[str] = set()

    # 1. コースデータ内の concepts.status == "mastered"
    for c in course_data.get("concepts", []):
        if c.get("status") == "mastered":
            name = c.get("name", "")
            if name:
                mastered.add(name)

    # 2. PostgreSQL learner_mastered_concepts
    try:
        from core.postgres import get_session as _pg_session
        from sqlalchemy import text as sa_text

        session = _pg_session()
        try:
            rows = session.execute(
                sa_text("""
                    SELECT lmc.concept_id
                    FROM learner_mastered_concepts lmc
                    JOIN learner_profiles lp ON lmc.learner_id = lp.id
                    WHERE lp.user_id = CAST(:user_id AS uuid)
                """),
                {"user_id": user_id},
            ).fetchall()
            for row in rows:
                if row[0]:
                    mastered.add(row[0])
        finally:
            session.close()
    except Exception:
        logger.warning("Failed to fetch learner_mastered_concepts", exc_info=True)

    # 3. チャット履歴があるトピックの関連概念を習得済みとみなす
    try:
        from core.postgres import get_session as _pg_session
        from sqlalchemy import text as sa_text

        session = _pg_session()
        try:
            rows = session.execute(
                sa_text("""
                    SELECT topic_id FROM learning_chat_history
                    WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                """),
                {"user_id": user_id, "course_id": course_id},
            ).fetchall()
            studied_topic_ids = {r[0] for r in rows}
        finally:
            session.close()

        # 学習済みトピックに紐づく前提知識概念を mastered に追加
        for t in course_data.get("topics", []):
            if t.get("id") in studied_topic_ids:
                for p in t.get("prerequisites", []):
                    name = p.get("name", p) if isinstance(p, dict) else str(p)
                    if name:
                        mastered.add(name)
    except Exception:
        logger.warning("Failed to fetch chat history for mastery inference", exc_info=True)

    return mastered
