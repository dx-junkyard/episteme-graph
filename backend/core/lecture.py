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

_SPOKEN_TEXT_PROMPT = """あなたは大学院レベルの学習を支援する教育者AIです。

以下の「チャンクテキスト」は、PDFから抽出された教材の一部であり、OCRノイズや数式の欠落が含まれる不完全なテキストです。
このテキストが意図している学術的な主張を推測し、その学問分野の標準的な知識に基づいて、正確で論理的な講義スクリプトを再構築してください。

## コース情報
- タイトル: {course_title}
- 到達目標: {course_goal}
- 対象となる前提知識: {course_prerequisites}

## コースの全体構成（マップ）
{course_structure}

## 今回処理するチャンク情報
- チャンク番号: {chunk_index} 番目（コース全体の教材順序における位置）

## チャンクテキスト (不完全な抽出テキスト):
{chunk_text}


## 指示:
1. **spoken_text**: 上記の「現在地」の文脈を踏まえ、このチャンクがコース全体の中で果たすべき役割（導入、詳細解説、まとめ等）を意識して、音声読み上げ用のテキストを構築してください。
   - 抽出の欠落や論理の飛躍がある場合は、該当分野の標準知識を用いて、前後の文脈と整合するように補完してください。
   - LaTeX 数式は自然言語に変換する（例: `$E = mc^2$` → 「Eイコールmcの二乗」）
   - 専門用語にはふりがなや読み方を含める
   - 段落の区切りは自然な「間」を表す「...」を入れる
   - 参照番号や図表番号は省略してよい
2. **formulas**: スクリプトに登場する重要な数式をリストアップしてください。
   - `id`: "formula_0", "formula_1", ... の連番
   - `latex`: 元の LaTeX 表記（**必須** — 絶対に省略・改名しないこと）
   - `spoken`: 音声読み上げ用のテキスト（**必須** — 絶対に省略・改名しないこと）

## 出力形式 (厳密にJSON):
{{
  "spoken_text": "...",
  "formulas": [
    {{"id": "formula_0", "latex": "E = mc^2", "spoken": "Eイコールmcの二乗"}}
  ]
}}

## 重要:
- JSON のみを出力してください。マークダウンコードフェンスは不要です。
- formulas の各要素には必ず `latex` と `spoken` の両方のキーを含めてください。
- `latex` を `formula`・`expression`・`math` などに改名してはいけません。
- `spoken` を `reading`・`text`・`description` などに改名してはいけません。
"""


_FORMULA_REQUIRED_KEYS = {"latex", "spoken"}
_MAX_LLM_ATTEMPTS = 3
_LATEX_EXTRACTION_ERROR = "[LaTeX extraction error: manual completion required]"


def _parse_spoken_text_response(raw: str) -> dict:
    """LLM レスポンスをパースし、必須キーを検証する。

    Parameters
    ----------
    raw : str
        LLM の生出力テキスト

    Returns
    -------
    dict
        ``{"spoken_text": str, "formulas": list[dict]}``

    Raises
    ------
    ValueError
        JSON パース失敗または formulas 内の必須キー欠落時
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines)

    result = json.loads(cleaned, strict=False)

    formulas = result.get("formulas", [])
    for i, f in enumerate(formulas):
        missing = _FORMULA_REQUIRED_KEYS - set(f.keys())
        if missing:
            raise ValueError(
                f"formulas[{i}] is missing required keys: {missing}. Got: {list(f.keys())}"
            )

    return {
        "spoken_text": result.get("spoken_text", ""),
        "formulas": formulas,
    }

def _build_course_context_text(course_data: dict) -> str:
    """コースデータから章・トピック構成を文字列化する"""
    lines = []
    for i, ch in enumerate(course_data.get("chapters", [])):
        title = ch if isinstance(ch, str) else ch.get("title", "")
        lines.append(f"第{i+1}章: {title}")
        for t in course_data.get("topics", []):
            if t.get("chapter_index") == i:
                lines.append(f"  - トピック: {t.get('title', '')}")
    return "\n".join(lines)


def generate_spoken_text_and_formulas(
    chunk_text: str, 
    chunk_index: int = 0, 
    course_data: dict | None = None
) -> dict:
    """チャンクテキストから spoken_text と formulas を LLM で生成する。"""
    if not chunk_text or not chunk_text.strip():
        return {"spoken_text": "", "formulas": []}

    # コンテキスト情報の抽出
    course_title = "不明"
    course_goal = "特になし"
    course_prereqs = "特になし"
    course_structure = "不明"

    if course_data:
        course_title = course_data.get("title", course_title)
        course_goal = course_data.get("goal", course_goal)
        prereqs = course_data.get("prerequisites", [])
        if prereqs:
            course_prereqs = ", ".join(prereqs)
        course_structure = _build_course_context_text(course_data)

    params = get_llm_params("fast")
    prompt = _SPOKEN_TEXT_PROMPT.format(
        course_title=course_title,
        course_goal=course_goal,
        course_prerequisites=course_prereqs,
        course_structure=course_structure,
        chunk_index=chunk_index,
        chunk_text=chunk_text[:4000]
    )

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_LLM_ATTEMPTS + 1):
        try:
            raw = generate_text(
                messages=[{"role": "user", "content": prompt}],
                model=params["model"],
                reasoning_effort=params["reasoning_effort"],
            )
            return _parse_spoken_text_response(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            last_exc = exc
            logger.warning(
                "spoken_text generation attempt %d/%d failed validation: %s",
                attempt, _MAX_LLM_ATTEMPTS, exc,
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "spoken_text generation attempt %d/%d failed: %s",
                attempt, _MAX_LLM_ATTEMPTS, exc, exc_info=True,
            )

    logger.error(
        "spoken_text generation failed after %d attempts, using fallback. Last error: %s",
        _MAX_LLM_ATTEMPTS, last_exc,
    )
    return _fallback_spoken_text(chunk_text)


def _fallback_spoken_text(chunk_text: str) -> dict:
    """LLM が全試行失敗した場合のフォールバック: 数式を簡易的に置換する。

    LaTeX 抽出に失敗した数式には視認性の高いエラープレースホルダーを設定する。
    空文字列は使わない（教員が見落とすリスクを避けるため）。
    """
    formulas: list[dict] = []
    spoken = chunk_text

    # Display math $$...$$
    def _replace_display(m: re.Match) -> str:
        idx = len(formulas)
        raw_latex = m.group(1).strip()
        latex = raw_latex if raw_latex else _LATEX_EXTRACTION_ERROR
        formulas.append({
            "id": f"formula_{idx}",
            "latex": latex,
            "spoken": _LATEX_EXTRACTION_ERROR,
        })
        return f"（数式{idx + 1}）"

    spoken = re.sub(r"\$\$([\s\S]+?)\$\$", _replace_display, spoken)

    # Inline math $...$
    def _replace_inline(m: re.Match) -> str:
        idx = len(formulas)
        raw_latex = m.group(1).strip()
        latex = raw_latex if raw_latex else _LATEX_EXTRACTION_ERROR
        formulas.append({
            "id": f"formula_{idx}",
            "latex": latex,
            "spoken": _LATEX_EXTRACTION_ERROR,
        })
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
