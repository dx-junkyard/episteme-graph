"""Episteme Graph — インタラクティブ・レクチャーモード コアロジック (Issue #66)。

チャンクテキストから音声読み上げ用テキスト・数式メタデータを生成し、
コーストピックに紐づくチャンクのレクチャーシーケンスを構築する。
"""

from __future__ import annotations

import json
import logging
import re
import time

from sqlalchemy import text as sa_text

from core.course_data import (
    course_chapters,
    course_title as _course_title,
    course_topics,
    find_course_topic,
    lecture_studio_settings as _lecture_studio_settings,
)
from core.llm import generate_text, get_llm_params
from core.personas import persona_prompt

logger = logging.getLogger(__name__)


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

## 語り口設定:
{persona_instruction}

## 言語指定:
{language_instruction}

## 指示:
1. **display_text**: 画面表示用テキストを再構築してください。
   - 抽出テキスト内の欠落・OCRノイズ・崩れた数式を補正し、教材として自然に読める本文へ修復する
   - 本文中に数式の LaTeX コードを直接書かず、代わりに `[[FORMULA_0]]`, `[[FORMULA_1]]` のような一意のプレースホルダーを配置する
   - `$...$` や `$$...$$` などの LaTeX デリミタは display_text に絶対に使わないこと
   - 段落構造は維持し、必要に応じて文を補完してよい
2. **spoken_text**: 上記の「現在地」の文脈を踏まえ、このチャンクがコース全体の中で果たすべき役割（導入、詳細解説、まとめ等）を意識して、音声読み上げ用のテキストを構築してください。
   - 抽出の欠落や論理の飛躍がある場合は、該当分野の標準知識を用いて、前後の文脈と整合するように補完してください。
   - LaTeX 数式は自然言語に変換する（例: `E = mc^2` → 「Eイコールmcの二乗」）
   - 専門用語にはふりがなや読み方を含める
   - 段落の区切りは自然な「間」を表す「...」を入れる
   - 参照番号や図表番号は省略してよい
   - ソースが英語の場合、`spoken_text`は無理にカタカナや全角に変換せず、**自然な半角英語の文章（Natural English sentences）**として出力してください。
3. **formulas**: スクリプトに登場する重要な数式をリストアップしてください。
   - `id`: "[[FORMULA_0]]", "[[FORMULA_1]]", ... のように display_text に埋め込んだプレースホルダーと完全一致する文字列
   - `latex`: 元の LaTeX 表記（**必須** — 絶対に省略・改名しないこと）
   - `spoken`: 音声読み上げ用のテキスト（**必須** — 絶対に省略・改名しないこと）
   - `is_display`: ブロック数式（独立行）なら true、インライン数式なら false を指定（**必須**）

## 出力形式 (厳密にJSON):
{{
  "display_text": "エネルギーは [[FORMULA_0]] で表される。",
  "spoken_text": "エネルギーは Eイコールmcの二乗 で表される。",
  "formulas": [
    {{"id": "[[FORMULA_0]]", "latex": "E = mc^2", "spoken": "Eイコールmcの二乗", "is_display": false}}
  ]
}}

## 重要:
- JSON のみを出力してください。マークダウンコードフェンスは不要です。
- formulas の各要素には必ず `latex` と `spoken` の両方のキーを含めてください。
- `latex` を `formula`・`expression`・`math` などに改名してはいけません。
- `spoken` を `reading`・`text`・`description` などに改名してはいけません。
- display_text には `$` や `$$` を絶対に含めないでください。数式は必ず `[[FORMULA_N]]` プレースホルダーで表現してください。
"""


_LANGUAGE_LABELS: dict[str, str] = {"ja": "日本語", "en": "English"}


def _language_instruction(language: str) -> str:
    """言語切替 (migration 040 Phase 4) 用のプロンプト指示文を返す。

    spoken_text の生成言語のみを指定する。display_text（表示教材）はソースの言語のまま
    変更しない方針（§3-2: 翻訳は本機能のスコープ外）。
    """
    label = _LANGUAGE_LABELS.get(language, _LANGUAGE_LABELS["ja"])
    return (
        f"spoken_text は必ず{label}で書くこと。"
        "display_text はソーステキストの言語のまま変更しないこと（表示テキストの翻訳はしない）。"
    )


_FORMULA_REQUIRED_KEYS = {"latex", "spoken", "is_display"}
_MAX_LLM_ATTEMPTS = 3
_LATEX_EXTRACTION_ERROR = "[LaTeX extraction error: manual completion required]"
# 429 / ResourceExhausted 時のリトライ待機秒数 (指数バックオフ)
_RATE_LIMIT_BACKOFF_SECONDS = [30, 90]


def _is_rate_limit_error(exc: Exception) -> bool:
    """例外が API レート制限 (429/ResourceExhausted) かどうかを判定する。"""
    s = str(exc).lower()
    return "429" in s or "resource exhausted" in s or "resource_exhausted" in s


def _parse_spoken_text_response(raw: str) -> dict:
    """LLM レスポンスをパースし、必須キーを検証する。

    Parameters
    ----------
    raw : str
        LLM の生出力テキスト

    Returns
    -------
    dict
        ``{"display_text": str, "spoken_text": str, "formulas": list[dict]}``

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
        "display_text": result.get("display_text", ""),
        "spoken_text": result.get("spoken_text", ""),
        "formulas": formulas,
    }

def _build_course_context_text(course_data: dict) -> str:
    """コースデータから章・トピック構成を文字列化する"""
    lines = []
    for i, ch in enumerate(course_chapters(course_data)):
        title = ch if isinstance(ch, str) else ch.get("title", "")
        lines.append(f"第{i+1}章: {title}")
        for t in course_topics(course_data):
            if t.get("chapter_index") == i:
                lines.append(f"  - トピック: {t.get('title', '')}")
    return "\n".join(lines)


def generate_spoken_text_and_formulas(
    chunk_text: str,
    chunk_index: int = 0,
    course_data: dict | None = None,
    persona_id: str | None = None,
    language: str = "ja",
) -> dict:
    """チャンクテキストから display_text / spoken_text / formulas を LLM で生成する。

    Parameters
    ----------
    language : str
        spoken_text の生成言語 (``"ja"`` / ``"en"``)。既定は ``"ja"``（後方互換）。
        display_text（表示教材）はソースの言語のまま維持され、翻訳されない（§3-2）。
    """
    if not chunk_text or not chunk_text.strip():
        return {"display_text": "", "spoken_text": "", "formulas": []}

    # コンテキスト情報の抽出
    course_title = "不明"
    course_goal = "特になし"
    course_prereqs = "特になし"
    course_structure = "不明"

    if course_data:
        course_title = _course_title(course_data, default=course_title)
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
        chunk_text=chunk_text[:4000],
        persona_instruction=persona_prompt(persona_id, target="narration") or "指定なし。通常の自然な講義調で生成してください。",
        language_instruction=_language_instruction(language),
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
            if _is_rate_limit_error(exc) and attempt < _MAX_LLM_ATTEMPTS:
                wait_sec = _RATE_LIMIT_BACKOFF_SECONDS[attempt - 1]
                logger.warning(
                    "spoken_text generation attempt %d/%d rate-limited (429). "
                    "Waiting %ds before retry. chunk_index=%d",
                    attempt, _MAX_LLM_ATTEMPTS, wait_sec, chunk_index,
                )
                time.sleep(wait_sec)
            else:
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

    display_text にはプレースホルダー方式 ``[[FORMULA_N]]`` を使用する。
    """
    formulas: list[dict] = []
    display = chunk_text
    spoken = chunk_text

    # Display math $$...$$
    def _replace_display_disp(m: re.Match) -> str:
        idx = len(formulas)
        raw_latex = m.group(1).strip()
        latex = raw_latex if raw_latex else _LATEX_EXTRACTION_ERROR
        formulas.append({
            "id": f"[[FORMULA_{idx}]]",
            "latex": latex,
            "spoken": _LATEX_EXTRACTION_ERROR,
            "is_display": True,
        })
        return f"[[FORMULA_{idx}]]"

    display = re.sub(r"\$\$([\s\S]+?)\$\$", _replace_display_disp, display)

    def _replace_display_spoken(m: re.Match) -> str:
        # formulas は既に _replace_display_disp で追加済みなのでカウントだけ合わせる
        nonlocal _spoken_formula_idx
        idx = _spoken_formula_idx
        _spoken_formula_idx += 1
        return f"（数式{idx + 1}）"

    _spoken_formula_idx = 0
    spoken = re.sub(r"\$\$([\s\S]+?)\$\$", _replace_display_spoken, spoken)

    # Inline math $...$
    def _replace_inline_disp(m: re.Match) -> str:
        idx = len(formulas)
        raw_latex = m.group(1).strip()
        latex = raw_latex if raw_latex else _LATEX_EXTRACTION_ERROR
        formulas.append({
            "id": f"[[FORMULA_{idx}]]",
            "latex": latex,
            "spoken": _LATEX_EXTRACTION_ERROR,
            "is_display": False,
        })
        return f"[[FORMULA_{idx}]]"

    display = re.sub(r"\$([^\$\n]+?)\$", _replace_inline_disp, display)

    def _replace_inline_spoken(m: re.Match) -> str:
        nonlocal _spoken_formula_idx
        idx = _spoken_formula_idx
        _spoken_formula_idx += 1
        return f"（数式{idx + 1}）"

    spoken = re.sub(r"\$([^\$\n]+?)\$", _replace_inline_spoken, spoken)

    return {"display_text": display, "spoken_text": spoken, "formulas": formulas}


# ---------------------------------------------------------------------------
# 旧フォーマット → プレースホルダー方式への正規化
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\[\[FORMULA_\d+\]\]")


def normalize_to_placeholder_format(
    display_text: str,
    formulas: list[dict],
) -> tuple[str, list[dict]]:
    """旧フォーマット（LaTeX デリミタ方式）のデータをプレースホルダー方式に変換する。

    既にプレースホルダー方式のデータはそのまま返す。

    Parameters
    ----------
    display_text : str
        画面表示用テキスト（旧形式: ``$...$`` / ``$$...$$`` を含む場合がある）
    formulas : list[dict]
        数式メタデータのリスト

    Returns
    -------
    tuple[str, list[dict]]
        正規化済みの (display_text, formulas)
    """
    if not display_text:
        return display_text, formulas

    # 既にプレースホルダー方式なら何もしない
    if _PLACEHOLDER_RE.search(display_text):
        return display_text, formulas

    # LaTeX デリミタが含まれていなければ変換不要
    if "$$" not in display_text and "$" not in display_text:
        return display_text, formulas

    # 旧フォーマットをプレースホルダーに変換
    new_formulas: list[dict] = []
    new_display = display_text

    # Display math $$...$$ → [[FORMULA_N]]
    def _replace_display(m: re.Match) -> str:
        idx = len(new_formulas)
        raw_latex = m.group(1).strip()
        # 旧 formulas から spoken を探す
        spoken = _find_spoken_for_latex(raw_latex, formulas)
        new_formulas.append({
            "id": f"[[FORMULA_{idx}]]",
            "latex": raw_latex,
            "spoken": spoken,
            "is_display": True,
        })
        return f"[[FORMULA_{idx}]]"

    new_display = re.sub(r"\$\$([\s\S]+?)\$\$", _replace_display, new_display)

    # Inline math $...$ → [[FORMULA_N]]
    def _replace_inline(m: re.Match) -> str:
        idx = len(new_formulas)
        raw_latex = m.group(1).strip()
        spoken = _find_spoken_for_latex(raw_latex, formulas)
        new_formulas.append({
            "id": f"[[FORMULA_{idx}]]",
            "latex": raw_latex,
            "spoken": spoken,
            "is_display": False,
        })
        return f"[[FORMULA_{idx}]]"

    new_display = re.sub(r"\$([^\$\n]+?)\$", _replace_inline, new_display)

    return new_display, new_formulas


def _find_spoken_for_latex(latex: str, formulas: list[dict]) -> str:
    """旧 formulas リストから LaTeX に対応する spoken テキストを探す。"""
    for f in formulas:
        if f.get("latex", "").strip() == latex:
            return f.get("spoken", "")
    return ""


# ---------------------------------------------------------------------------
# スライド分割 (migration 040: レクチャースライド同期 Phase 1)
# ---------------------------------------------------------------------------

# スライド区切りマーカー: 単独行の "===" （3個以上の連続 "=" を許容、行頭行末の空白も許容）
_SLIDE_MARKER_RE = re.compile(r"^[ \t]*={3,}[ \t]*$", re.MULTILINE)
_SLIDE_FORMULA_ID_RE = re.compile(r"\[\[FORMULA_\d+\]\]")


def _split_marker_segments(text: str | None) -> list[str]:
    """テキストをスライド区切りマーカーで分割し、空セグメントを除去して返す。"""
    if not text:
        return []
    raw_segments = _SLIDE_MARKER_RE.split(text)
    segments = [seg.strip() for seg in raw_segments]
    return [seg for seg in segments if seg]


def _assign_slide_formulas(
    slide_texts: list[tuple[str, str | None]],
    formulas: list[dict],
) -> list[list[dict]]:
    """formulas を、各スライドの display_text が参照する [[FORMULA_N]] にのみ割り当てる。

    どのスライドからも参照されない formulas（id が無い、あるいはどの display_text にも
    現れない）は最後のスライドに付与し、情報を落とさない
    （全スライドの formulas の和 == 入力 formulas を保証）。
    """
    slide_formula_lists: list[list[dict]] = [[] for _ in slide_texts]
    if not slide_texts:
        return slide_formula_lists

    ids_per_slide = [
        set(_SLIDE_FORMULA_ID_RE.findall(display)) for display, _spoken in slide_texts
    ]

    unassigned: list[dict] = []
    for formula in formulas:
        fid = str(formula.get("id") or "") if isinstance(formula, dict) else ""
        assigned = False
        if fid:
            for i, ids in enumerate(ids_per_slide):
                if fid in ids:
                    slide_formula_lists[i].append(formula)
                    assigned = True
                    break
        if not assigned:
            unassigned.append(formula)

    if unassigned:
        slide_formula_lists[-1].extend(unassigned)

    return slide_formula_lists


def split_slides(
    display_text: str | None,
    spoken_text: str | None,
    formulas: list | None = None,
) -> tuple[list[dict], bool]:
    """display_text / spoken_text をスライド区切りマーカー ``===`` で分割する。

    分割結果は DB に保存せず、読み出し時に決定論的に導出する（§2-2）。

    Parameters
    ----------
    display_text : str | None
        画面表示用テキスト。None/空の場合のフォールバック（``text`` 列などへの代替）は
        呼び出し側の責務とする（既存の ``display_text or text`` パターンを踏襲）。
    spoken_text : str | None
        音声読み上げ用テキスト。None/空の場合は各スライドの spoken_text は None になる。
    formulas : list[dict] | None
        数式メタデータのリスト。各スライドの display_text が参照する [[FORMULA_N]]
        の分だけ割り当てられる。未参照分は最後のスライドに付与する。

    Returns
    -------
    tuple[list[dict], bool]
        ``(slides, mismatch)``。``slides`` の各要素は
        ``{"slide_index": int, "display_text": str, "spoken_text": str | None, "formulas": list}``。
        ``mismatch`` は表示と読み上げの分割数が一致せず 1 スライドに縮退した場合に True。
    """
    formulas = list(formulas) if formulas else []

    display_segments = _split_marker_segments(display_text)
    if not display_segments:
        # display_text が None/空、またはマーカーのみで実質空 → 1件の空スライド
        display_segments = [""]

    spoken_segments = _split_marker_segments(spoken_text)

    if not spoken_segments:
        # spoken_text が無い/空: display の分割数でスライドを作り spoken_text=None
        slide_texts: list[tuple[str, str | None]] = [(d, None) for d in display_segments]
        mismatch = False
    elif len(display_segments) == len(spoken_segments):
        slide_texts = list(zip(display_segments, spoken_segments))
        mismatch = False
    else:
        # 分割数不一致 → 1スライドに縮退（マーカー除去済み全文どうしをペア）。情報は落とさない。
        merged_display = "\n\n".join(display_segments)
        merged_spoken = "\n\n".join(spoken_segments)
        slide_texts = [(merged_display, merged_spoken)]
        mismatch = True

    slide_formula_lists = _assign_slide_formulas(slide_texts, formulas)

    slides = [
        {
            "slide_index": i,
            "display_text": display,
            "spoken_text": spoken,
            "formulas": slide_formula_lists[i],
        }
        for i, (display, spoken) in enumerate(slide_texts)
    ]
    return slides, mismatch


def count_slide_marker_segments(
    display_text: str | None,
    spoken_text: str | None,
) -> tuple[int, int]:
    """display_text / spoken_text をスライド区切りマーカーで分割した場合のセグメント数を返す。

    ``split_slides()`` 本体の分割・縮退ロジックには影響しない読み取り専用の補助関数。
    原稿スタジオのプレビュー（教員向け整合インジケータ「表示 N 枚 / 読み上げ M 区切り」）が
    サーバ側の分割結果だけで表示を組み立てられるようにするために存在する
    （Tier2-11: プレビューはクライアント側で分割を再実装しない）。

    Returns
    -------
    tuple[int, int]
        ``(display_segment_count, spoken_segment_count)``。
    """
    return len(_split_marker_segments(display_text)), len(_split_marker_segments(spoken_text))


def get_course_lecture_language(course_data: dict | None) -> str:
    """コースのレクチャースタジオ設定から読み上げ言語 (``ja``/``en``) を取得する。

    設定が無い、または ``lecture_language`` 未設定の場合は既定の ``"ja"`` を返す。
    """
    language = _lecture_studio_settings(course_data).get("lecture_language")
    if language:
        return str(language)
    return "ja"


# ---------------------------------------------------------------------------
# 音声 readiness 判定 (Tier2-11: 講義系の判定共通化)
# ---------------------------------------------------------------------------
#
# 「音声準備完了」の判定はスライド単位 + 言語一致を正本とする（旧: chunk 単位のみの
# 粗い判定と、この slide+language 判定の2実装が並存し、G層 To-Do（旧 chunk 単位）と
# 学習画面のレクチャーボタン活性判定（この関数の旧個別実装）が食い違い得た）。
# ``core/status/projector.py::project_course_status`` と
# ``api/routes/lecture.py::get_topic_audio_status`` の両方から本関数を呼ぶ。
# トピック単位のドラフト判定（``_topic_has_linkable_material`` 経由の早期リターン）は
# 呼び出し側の責務のままとし、本関数は「material_ids に属する chunks の音声 readiness」
# という下位の判定だけに責務を限定する。


_AUDIO_READINESS_EMPTY: dict = {
    "total_chunks": 0,
    "generated_chunks": 0,
    "ready_chunks": 0,
    "total_slides": 0,
    "ready_slides": 0,
    "stale_language": False,
}


def compute_material_audio_readiness(
    session,
    material_ids: list[str],
    target_language: str,
    voice: str = "alloy",
) -> dict:
    """教材集合（material_ids に属する chunks）の音声 readiness を判定する（唯一の正本）。

    スライド単位（``split_slides()`` による分割）+ 言語一致で判定する。1チャンクが
    複数スライドに分割される場合、一部スライドのみ音声キャッシュがある／言語が
    コース設定と異なる、といったケースを取りこぼさない。

    Parameters
    ----------
    session : sqlalchemy.orm.Session
        呼び出し側が用意する DB セッション（本関数はクローズしない）。
    material_ids : list[str]
        対象教材 (``chunks.material_id``) の ID 一覧。空なら即座に空の結果を返す。
    target_language : str
        コースの読み上げ言語（``get_course_lecture_language()`` の結果）。
    voice : str
        対象とする音声キャッシュの voice（既定 ``"alloy"``。学習画面の再生ボイス固定に合わせる）。

    Returns
    -------
    dict
        ``{"total_chunks": int, "generated_chunks": int, "ready_chunks": int,
        "total_slides": int, "ready_slides": int, "stale_language": bool}``。

        - ``generated_chunks``: ``spoken_text`` が生成済みのチャンク数（script readiness 用）。
        - ``ready_chunks``: 言語不問でスライド音声が1つ以上キャッシュ済みのチャンク数。
        - ``ready_slides``: そのうち ``target_language`` と一致するスライド音声の数
          （呼び出し側の readiness 判定は基本的に ``ready_slides > 0`` / ``ready_slides ==
          total_slides`` を使う）。
        - ``stale_language``: キャッシュはあるが言語が ``target_language`` と異なる
          チャンクが1件以上ある場合に True。
    """
    if not material_ids:
        return dict(_AUDIO_READINESS_EMPTY)

    mid_placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
    mid_params: dict = {f"mid_{i}": mid for i, mid in enumerate(material_ids)}
    chunk_rows = session.execute(
        sa_text(f"""
            SELECT c.id, c.display_text, c.text, c.spoken_text, c.formulas, c.spoken_language
            FROM chunks c
            WHERE c.material_id IN ({mid_placeholders})
              AND c.text IS NOT NULL AND c.text != ''
        """),
        mid_params,
    ).fetchall()

    if not chunk_rows:
        return dict(_AUDIO_READINESS_EMPTY)

    chunk_ids = [str(row[0]) for row in chunk_rows]
    audio_placeholders = ", ".join(f":cid_{i}" for i in range(len(chunk_ids)))
    audio_params: dict = {f"cid_{i}": cid for i, cid in enumerate(chunk_ids)}
    audio_params["voice"] = voice
    audio_rows = session.execute(
        sa_text(f"""
            SELECT chunk_id, slide_index
            FROM lecture_audio_cache
            WHERE chunk_id IN ({audio_placeholders}) AND voice = :voice
        """),
        audio_params,
    ).fetchall()
    audio_slide_set = {(str(row[0]), int(row[1])) for row in audio_rows}

    total_chunks = len(chunk_rows)
    generated_chunks = sum(1 for row in chunk_rows if row[3])
    total_slides = 0
    ready_slides = 0
    ready_chunks = 0
    stale_language = False

    for row in chunk_rows:
        chunk_id = str(row[0])
        display_text = row[1] or row[2] or ""
        spoken_text = row[3]
        formulas = row[4] if row[4] else []
        chunk_language = row[5] or "ja"

        slides, _mismatch = split_slides(display_text, spoken_text, formulas)
        total_slides += len(slides)

        chunk_has_cached_slide = False
        for slide in slides:
            if (chunk_id, slide["slide_index"]) not in audio_slide_set:
                continue
            chunk_has_cached_slide = True
            if chunk_language == target_language:
                ready_slides += 1

        if chunk_has_cached_slide:
            ready_chunks += 1
            if chunk_language != target_language:
                stale_language = True

    return {
        "total_chunks": total_chunks,
        "generated_chunks": generated_chunks,
        "ready_chunks": ready_chunks,
        "total_slides": total_slides,
        "ready_slides": ready_slides,
        "stale_language": stale_language,
    }


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
    topic_info = find_course_topic(course_data, topic_id)

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
        for t in course_topics(course_data):
            if t.get("id") in studied_topic_ids:
                for p in t.get("prerequisites", []):
                    name = p.get("name", p) if isinstance(p, dict) else str(p)
                    if name:
                        mastered.add(name)
    except Exception:
        logger.warning("Failed to fetch chat history for mastery inference", exc_info=True)

    return mastered
