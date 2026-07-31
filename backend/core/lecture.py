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
    course_source_material_ids,
    course_title as _course_title,
    course_topics,
    find_course_topic,
    iter_all_topics,
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
このテキストが意図している学術的な主張を推測し、その学問分野の標準的な知識に基づいて、
学習者が画面で読む「教材本文（display_text）」と、それを先生が口頭で説明する「読み上げ原稿（spoken_text）」の
2つを作成してください。この2つは役割が異なるため、同じ文をそのまま流用してはいけません。

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
1. **display_text（画面に表示する教材本文）**: 学習者が画面で読む「教材」そのものを再構築してください。
   - これは読み上げ原稿ではなく、教科書・スライドに載る学習内容です。原文（抽出テキスト）の内容・構造・用語に忠実に整える
   - 抽出テキスト内の欠落・OCRノイズ・崩れた数式のみを補正し、原文に無い解説・具体例・話題を勝手に足さない
   - 教科書のような記述体で書く。「〜しましょう」「〜ですね」等の話しかけ・ナレーション調は使わない
   - 本文中に数式の LaTeX コードを直接書かず、代わりに `[[FORMULA_0]]`, `[[FORMULA_1]]` のような一意のプレースホルダーを配置する
   - `$...$` や `$$...$$` などの LaTeX デリミタは display_text に絶対に使わないこと
   - 段落構造は維持し、必要に応じて文を補完してよい
2. **spoken_text（音声で読み上げるナレーション）**: 上記 display_text の教材を、先生が口頭で説明するための読み上げ原稿を構築してください。
   - **display_text をそのまま読み上げてはいけません。** 教材の内容を噛み砕き、導入・要点の言い換え・補足・次への橋渡しを加えた、自然な話し言葉のナレーションにする
   - ただし display_text が扱う範囲・順序に沿って説明し、別の話題を始めない（表示と読み上げが同じ内容を指すようにするため）
   - このチャンクがコース全体で果たす役割（導入、詳細解説、まとめ等）を意識する
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
  "display_text": "質量とエネルギーは等価であり、[[FORMULA_0]] で表される。",
  "spoken_text": "ここでは質量とエネルギーの関係を確認しましょう。この2つは本質的に等価で、式にすると、Eイコールmcの二乗、という形で表されます... この式が何を意味するのかを順に見ていきます。",
  "formulas": [
    {{"id": "[[FORMULA_0]]", "latex": "E = mc^2", "spoken": "Eイコールmcの二乗", "is_display": false}}
  ]
}}

## 重要:
- JSON のみを出力してください。マークダウンコードフェンスは不要です。
- display_text（教材本文）と spoken_text（読み上げ）は同じ文の使い回しにせず、役割に応じて必ず書き分けてください。
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
    model: str | None = None,
) -> dict:
    """チャンクテキストから display_text / spoken_text / formulas を LLM で生成する。

    Parameters
    ----------
    language : str
        spoken_text の生成言語 (``"ja"`` / ``"en"``)。既定は ``"ja"``（後方互換）。
        display_text（表示教材）はソースの言語のまま維持され、翻訳されない（§3-2）。
    model : str | None
        M層 Phase 3（この実行だけのモデル上書き, scene "lecture_studio"）。呼び出し側
        （``routes/lecture_studio/scripts.py``）が :func:`core.llm_policy.validate_model_for_scene`
        で検証済みの値のみを渡す前提。``None`` は従来どおり fast tier 固定
        （挙動不変。バックグラウンドスレッド実行のため contextvar の
        ``model_override`` ではなく明示引数で伝搬する）。
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

    effective_model = model or params["model"]
    effective_effort = None if model else params["reasoning_effort"]

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_LLM_ATTEMPTS + 1):
        try:
            raw = generate_text(
                messages=[{"role": "user", "content": prompt}],
                model=effective_model,
                reasoning_effort=effective_effort,
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
# 図プレースホルダー（Phase 4 図のコース流通 §7.2）。数式の [[FORMULA_N]] と同方式。
_SLIDE_FIGURE_ID_RE = re.compile(r"\[\[FIGURE_\d+\]\]")
# 未解決の図埋め込み記法（resolve_figure_embeds 未適用、または figures_by_id に無く
# 原文のまま残った場合）。定義は「図記法の解決」セクションで行う（本モジュール下方）。
_FIGURE_EMBED_RE = re.compile(r"!\[\[figure:([^\]]+)\]\]")


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


def _assign_slide_figures(
    slide_texts: list[tuple[str, str | None]],
    figures: list[dict],
) -> list[list[dict]]:
    """figures を、各スライドの display_text が参照する [[FIGURE_N]] にのみ割り当てる。

    ``_assign_slide_formulas`` と同じ規則（未参照分は最後のスライドに残し、
    情報を落とさない。全スライドの figures の和 == 入力 figures を保証）。
    """
    slide_figure_lists: list[list[dict]] = [[] for _ in slide_texts]
    if not slide_texts:
        return slide_figure_lists

    ids_per_slide = [
        set(_SLIDE_FIGURE_ID_RE.findall(display)) for display, _spoken in slide_texts
    ]

    unassigned: list[dict] = []
    for figure in figures:
        fid = str(figure.get("id") or "") if isinstance(figure, dict) else ""
        assigned = False
        if fid:
            for i, ids in enumerate(ids_per_slide):
                if fid in ids:
                    slide_figure_lists[i].append(figure)
                    assigned = True
                    break
        if not assigned:
            unassigned.append(figure)

    if unassigned:
        slide_figure_lists[-1].extend(unassigned)

    return slide_figure_lists


def split_slides(
    display_text: str | None,
    spoken_text: str | None,
    formulas: list | None = None,
    figures: list | None = None,
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
    figures : list[dict] | None
        図メタデータのリスト（Phase 4 図のコース流通 §7.2）。各スライドの display_text
        が参照する [[FIGURE_N]] の分だけ割り当てられる。未参照分は最後のスライドに付与する
        （``formulas`` と同じ規則）。

    Returns
    -------
    tuple[list[dict], bool]
        ``(slides, mismatch)``。``slides`` の各要素は
        ``{"slide_index": int, "display_text": str, "spoken_text": str | None,
        "formulas": list, "figures": list}``。
        ``mismatch`` は表示と読み上げの分割数が一致せず 1 スライドに縮退した場合に True。
    """
    formulas = list(formulas) if formulas else []
    figures = list(figures) if figures else []

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
    slide_figure_lists = _assign_slide_figures(slide_texts, figures)

    slides = [
        {
            "slide_index": i,
            "display_text": display,
            "spoken_text": spoken,
            "formulas": slide_formula_lists[i],
            "figures": slide_figure_lists[i],
        }
        for i, (display, spoken) in enumerate(slide_texts)
    ]
    return slides, mismatch


# ---------------------------------------------------------------------------
# 自動ページ分割 (long topic 教材 を複数スライドへ)
# ---------------------------------------------------------------------------
#
# `===` マーカーが無い長いトピック教材を、読みやすいスライド（ページ）に自動分割する。
# 表示（display）と読み上げ（spoken）は別テキストになり得るため、両者を同じ page 数・
# 同じ意味順で分割して slide_index を対応させることが不変条件（表示スライドと音声スライドの
# 同期を壊さない）。この分割は「トピック教材ベースのレクチャー」（本モジュールの
# `build_topic_slides`）と、そこから音声を作る studio 側の両方が**同じ関数**を通ることで
# 一致を保証する（決定論的・LLM 非使用）。

_FORMULA_PLACEHOLDER_LEN = 60  # [[FORMULA_N]] 1個の表示長換算（§4-1 の目安に合わせる）
_FIGURE_PLACEHOLDER_LEN = 200  # [[FIGURE_N]] 1個の表示長換算（図1個=200字、Phase 4 §7.2）
DEFAULT_SLIDE_MAX_CHARS = 600  # スライド1枚の display 目安（§4-1）
_JP_SENTENCE_ENDERS = "。．！？"


def _sentence_units(text: str) -> list[str]:
    """テキストを文単位に分割する（日本語 ``。．！？`` と、空白/行末が続く英語 ``.!?`` の両対応）。

    ``3.14`` のような小数で切らないよう、英語ピリオドは直後が空白/改行/末尾のときのみ文末とみなす。
    """
    out: list[str] = []
    buf = ""
    for i, ch in enumerate(text):
        buf += ch
        if ch in _JP_SENTENCE_ENDERS:
            out.append(buf)
            buf = ""
        elif ch in ".!?":
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt in (" ", "\n", "\t", ""):
                out.append(buf)
                buf = ""
    if buf.strip():
        out.append(buf)
    return [s.strip() for s in out if s.strip()]


def _display_length(text: str | None) -> int:
    """``[[FORMULA_N]]`` / ``[[FIGURE_N]]`` / 未解決の ``![[figure:...]]`` を
    一定長に換算した表示長を返す（ページ分割の目安計算用。図1個=200字、Phase 4 §7.2）。

    解決済み ``[[FIGURE_N]]`` と未解決 ``![[figure:id]]``（呼び出し側が figures_by_id を
    供給しない場合はこちらのまま残る — ``build_topic_slides`` の readiness/音声生成側の
    呼び出し等）の両方を同じ 200字換算にすることで、figures_by_id の有無によって
    ページ分割の境界がずれない（表示・音声生成・readiness の slide_index 一致を壊さない）。
    """
    if not text:
        return 0
    formula_count = len(_SLIDE_FORMULA_ID_RE.findall(text))
    figure_count = len(_SLIDE_FIGURE_ID_RE.findall(text)) + len(_FIGURE_EMBED_RE.findall(text))
    plain = _SLIDE_FORMULA_ID_RE.sub("", text)
    plain = _SLIDE_FIGURE_ID_RE.sub("", plain)
    plain = _FIGURE_EMBED_RE.sub("", plain)
    return (
        len(plain)
        + formula_count * _FORMULA_PLACEHOLDER_LEN
        + figure_count * _FIGURE_PLACEHOLDER_LEN
    )


def _paragraph_units(text: str | None) -> list[str]:
    """テキストを段落単位に分割する（空行→単一改行→文、の順で粒度を落とす）。

    ``[[FORMULA_N]]`` プレースホルダーは1トークンとして段落内に残す（数式を割らない）。
    """
    if not text or not text.strip():
        return []
    parts = [p.strip() for p in re.split(r"\n[ \t]*\n", text.strip()) if p.strip()]
    if len(parts) >= 2:
        return parts
    lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()]
    if len(lines) >= 2:
        return lines
    sents = _sentence_units(text.strip())
    return sents if sents else [text.strip()]


def _group_units_by_size(units: list[str], max_chars: int) -> list[tuple[int, int]]:
    """段落を表示長 ``max_chars`` 以下のページ（連続 index 範囲）に貪欲にまとめる。"""
    groups: list[tuple[int, int]] = []
    start = 0
    cur = 0
    for i, unit in enumerate(units):
        ulen = _display_length(unit)
        if i > start and cur + ulen > max_chars:
            groups.append((start, i))
            start = i
            cur = 0
        cur += ulen
    groups.append((start, len(units)))
    return groups


def _balance_units_into_n(units: list[str], n: int) -> list[tuple[int, int]] | None:
    """段落をちょうど ``n`` 個の非空ページ（連続 index 範囲）に均す。作れなければ None。

    表示（display）が n ページに分かれたとき、読み上げ（spoken）も同数ページに揃えるための
    比例分割。段落数が n 未満のときは None（呼び出し側は音声なし=タイマー送りに縮退する）。
    """
    if n <= 0 or len(units) < n:
        return None
    lengths = [_display_length(u) for u in units]
    total = sum(lengths) or 1
    target = total / n
    ranges: list[tuple[int, int]] = []
    start = 0
    acc = 0
    for i in range(len(units)):
        acc += lengths[i]
        groups_open = len(ranges)
        remaining_units = len(units) - (i + 1)
        remaining_groups = n - groups_open - 1
        # 残りページに最低1段落ずつ残せなくなる直前で必ず閉じる（ちょうど n ページを保証）。
        force = remaining_units == remaining_groups
        if groups_open < n - 1 and (force or acc >= target):
            ranges.append((start, i + 1))
            start = i + 1
            acc = 0
    ranges.append((start, len(units)))
    if len(ranges) != n or any(s >= e for s, e in ranges):
        return None
    return ranges


def auto_paginate_slides(
    display_text: str | None,
    spoken_text: str | None,
    formulas: list | None = None,
    max_chars: int = DEFAULT_SLIDE_MAX_CHARS,
    figures: list | None = None,
) -> tuple[list[dict], bool]:
    """``split_slides`` を自動ページ分割で包む（長いトピック教材を複数スライドへ）。

    - display / spoken に ``===`` マーカーがあれば、教員の明示分割を優先して
      ``split_slides`` にそのまま委譲する（自動分割しない）。
    - マーカーが無く display が ``max_chars`` を超える場合のみ、段落境界で複数ページに分割し、
      表示と読み上げを同数ページ・同じ意味順で対応させてから ``split_slides`` に渡す。
      spoken を同数ページに割れない場合は spoken を空（タイマー送り）に縮退させ、表示だけを
      ページ分割する（同期を壊さないための安全側）。
    - それ以外は従来どおり 1 スライド（``split_slides`` に委譲）。

    ``figures``（Phase 4 図のコース流通 §7.2）は ``formulas`` と同様、display_text の
    ``[[FIGURE_N]]`` プレースホルダーを頼りに各ページへ割り当てられる
    （``_display_length`` が図1個=200字換算するため、ページ分割の目安計算にも反映される）。

    戻り値は ``split_slides`` と同形 ``(slides, mismatch)``。
    """
    if _SLIDE_MARKER_RE.search(display_text or "") or _SLIDE_MARKER_RE.search(spoken_text or ""):
        return split_slides(display_text, spoken_text, formulas, figures)

    if _display_length(display_text) <= max_chars:
        return split_slides(display_text, spoken_text, formulas, figures)

    units_d = _paragraph_units(display_text)
    if len(units_d) < 2:
        return split_slides(display_text, spoken_text, formulas, figures)

    groups_d = _group_units_by_size(units_d, max_chars)
    if len(groups_d) < 2:
        return split_slides(display_text, spoken_text, formulas, figures)

    n = len(groups_d)
    display_pages = ["\n\n".join(units_d[s:e]) for s, e in groups_d]

    spoken_pages: list[str] | None = None
    if spoken_text and spoken_text.strip():
        units_s = _paragraph_units(spoken_text)
        if len(units_s) == len(units_d):
            # 表示と読み上げが同じ段落構造 → 同じ index 境界で対応（完全整合）。
            spoken_pages = ["\n\n".join(units_s[s:e]) for s, e in groups_d]
        else:
            # 構造が異なる → n ページへ比例分割（近似整合）。割れなければタイマー送りへ。
            groups_s = _balance_units_into_n(units_s, n)
            if groups_s is not None:
                spoken_pages = ["\n\n".join(units_s[s:e]) for s, e in groups_s]

    display_marked = "\n===\n".join(display_pages)
    spoken_marked = "\n===\n".join(spoken_pages) if spoken_pages else ""
    return split_slides(display_marked, spoken_marked, formulas, figures)


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
# トピック教材ベースのレクチャー（表示ソース判定・スライド構築の正本）
# ---------------------------------------------------------------------------
#
# 受講表示（routes/lecture.py::_build_topic_draft_segment）・studio の音声生成
# （lecture_studio/scripts.py::_generate_course_topic_audio）・readiness 判定（下記）の
# 3者が同じ述語・同じ分割を通ることで slide_index を完全一致させる（migration 047）。
# 正本は core 側に置く（core から routes を import しない。N18）。


def topic_student_material(topic: dict) -> str:
    """トピックの授業用教材本文（表示ソース）を返す。student_material 最優先。"""
    material = topic.get("student_material")
    if isinstance(material, dict):
        text = str(material.get("source_text") or "").strip()
        if text:
            return text
    return str(topic.get("content") or topic.get("summary") or "").strip()


def topic_spoken_script(topic: dict) -> str:
    """トピックの読み上げ原稿（音声ソース）を返す。spoken_script 最優先。"""
    return str(topic.get("spoken_script") or topic.get("content") or topic.get("summary") or "").strip()


def lecture_uses_topic_material(topic: dict) -> bool:
    """このトピックのレクチャーをトピック教材（student_material/spoken_script）ベースで
    組むかどうかを判定する（唯一の正本）。

    受講画面の非レクチャー表示（``get_topic_material`` = student_material 最優先）と
    レクチャー表示を一致させるため、トピックが授業用の教材本文または読み上げ原稿を
    持つならトピック教材経路を使う。持たなければ PDF 由来チャンク経路へフォールバックする。
    ``get_lecture_sequence`` / ``get_topic_audio_status`` / トピック音声生成 / 状態投影
    （``core/status/projector.py``）が同じ判定を使うことで、表示・ボタン活性・音声生成・
    G層 To-Do の食い違いを防ぐ。
    """
    return bool(topic_student_material(topic) or topic_spoken_script(topic))


def _resolve_equation_embeds(
    text: str,
    evidence_links: list[dict],
    existing_formulas: list[dict],
) -> tuple[str, list[dict]]:
    """![[equation:xxx]] / [[equation:xxx]] 埋め込みを [[FORMULA_N]] プレースホルダーに変換する。

    evidence_links から LaTeX を取得できる場合はそれを使い、取得できない場合は
    埋め込みを空文字列に除去する（生テキストをフロントに渡さない）。
    """
    eq_by_id: dict[str, dict] = {}
    for link in (evidence_links or []):
        if link.get("kind") == "equation":
            tid = str(link.get("target_id") or "").strip()
            if tid:
                eq_by_id[tid] = link

    formulas = list(existing_formulas)
    formula_offset = len(formulas)

    def _replace(m: re.Match) -> str:
        eq_id = m.group(1).strip()
        link = eq_by_id.get(eq_id)
        latex = str(link.get("latex") or "") if link else ""
        summary = str(link.get("summary") or "") if link else ""
        plain_text = str(link.get("plain_text") or "") if link else ""
        idx = formula_offset + len(formulas) - len(existing_formulas)
        placeholder = f"[[FORMULA_{idx}]]"
        # 描画用 LaTeX は link.latex を最優先する。summary は意味要約（散文）の
        # 場合があり、それを LaTeX として埋め込むと数式描画が壊れる。
        body = latex or summary
        if body:
            formulas.append({
                "id": placeholder,
                "latex": body,
                # 読み上げは人間向けテキストを優先する（生 TeX を読み上げない）。
                "spoken": plain_text or summary or body,
                "is_display": True,
            })
            return placeholder
        # 解決できない場合は埋め込みを除去する（生テキストを見せない）
        return ""

    result = re.sub(r"!\[\[equation:([^\]]+)\]\]", _replace, text)
    result = re.sub(r"\[\[equation:([^\]]+)\]\]", _replace, result)
    return result, formulas


# ---------------------------------------------------------------------------
# 図記法の解決 (Phase 4: 図のコース流通, §7.2)
# ---------------------------------------------------------------------------
#
# 記法 ``![[figure:<figure_id>]]``（figure_id = document_figures.id の UUID）を
# ``[[FIGURE_N]]`` プレースホルダーに解決する。数式の ``![[equation:xxx]]`` →
# ``[[FORMULA_N]]`` 解決と同格。core は DB を読まないため、caption/image_url 等の
# 解決済みメタデータ（``figures_by_id``）は呼び出し側 routes が用意して渡す。
# （``_FIGURE_EMBED_RE`` はスライド分割セクションで定義済み — ``_display_length`` が
# 未解決 embed も 200字換算するために先出ししている）。


def find_figure_embed_ids(text: str | None) -> list[str]:
    """テキスト中の ``![[figure:<figure_id>]]`` 埋め込みが参照する figure_id 一覧を返す
    （出現順・重複除去、DB を読まない純関数）。

    学習者向け画像配信の「コース内容から実際に参照されているか」判定（§7.3 条件3）に使う。
    """
    if not text:
        return []
    seen: list[str] = []
    for m in _FIGURE_EMBED_RE.finditer(text):
        fig_id = m.group(1).strip()
        if fig_id and fig_id not in seen:
            seen.append(fig_id)
    return seen


def resolve_figure_embeds(
    text: str | None,
    figures_by_id: dict[str, dict] | None,
) -> tuple[str, list[dict]]:
    """``![[figure:<figure_id>]]`` 埋め込みを ``[[FIGURE_N]]`` プレースホルダーに解決する。

    ``figures_by_id`` に無い（未供給・未知の）figure_id の埋め込みは、情報を落とさない
    ために原文のまま残す（数式解決が「解決できなければ除去する」のとは異なる挙動 —
    図は教員が後から供給し得るため）。

    Parameters
    ----------
    figures_by_id : dict[str, dict] | None
        ``figure_id -> {"caption": ..., "image_url": ...}`` の解決済みメタデータ。
        DB は読まない（呼び出し側 routes が document_figures から用意する）。

    Returns
    -------
    tuple[str, list[dict]]
        ``(resolved_text, figures)``。``figures`` の各要素は
        ``{"id": "[[FIGURE_N]]", "figure_id": str, "caption": str | None,
        "explanation": None, "image_url": str | None}``。``id`` はスライド分割時の
        割り当て（``_assign_slide_figures``）に使うプレースホルダー対応キー。
        ``explanation`` は v1 では常に ``None``（後続エージェントが配線する）。
    """
    if not text:
        return text or "", []

    lookup = figures_by_id or {}
    figures: list[dict] = []

    def _replace(m: re.Match) -> str:
        fig_id = m.group(1).strip()
        meta = lookup.get(fig_id)
        if meta is None:
            # 未知/未供給の figure_id は原文のまま残す（情報を落とさない）。
            # 空 dict（{}）はキー自体は存在する＝供給済みとみなし解決する
            # （caption/image_url が未設定なだけ）。
            return m.group(0)
        idx = len(figures) + 1
        placeholder = f"[[FIGURE_{idx}]]"
        figures.append({
            "id": placeholder,
            "figure_id": fig_id,
            "caption": meta.get("caption"),
            "explanation": None,
            "image_url": meta.get("image_url"),
        })
        return placeholder

    resolved = _FIGURE_EMBED_RE.sub(_replace, text)
    return resolved, figures


def strip_figure_embeds(text: str | None) -> str:
    """読み上げ原稿から ``![[figure:...]]`` 埋め込みを除去する（図は読み上げない、§7.2）。"""
    if not text:
        return text or ""
    return _FIGURE_EMBED_RE.sub("", text)


def build_topic_slides(
    topic: dict,
    figures_by_id: dict[str, dict] | None = None,
) -> tuple[list[dict], str, str, list[dict]]:
    """トピック教材から、表示・音声・readiness が共有する正準スライドを決定論的に構築する。

    display=student_material、spoken=spoken_script（無ければ content/summary）を、数式
    プレースホルダー正規化・``![[equation:xxx]]`` 解決・``![[figure:xxx]]`` 解決
    （Phase 4 §7.2）のうえ ``auto_paginate_slides`` でスライド分割する（``===`` マーカーが
    あれば教員の明示分割を優先、無く長い場合は段落境界で自動ページ分割）。受講側
    （``_build_topic_draft_segment``）・studio の音声生成・readiness の3者がこの関数を
    通ることで ``slide_index`` を完全一致させる。**LLM を使わない＝決定論的**
    （同期パスに非決定性を入れない）。

    Parameters
    ----------
    figures_by_id : dict[str, dict] | None
        ``![[figure:xxx]]`` 埋め込み解決用の figure メタデータ（§7.2）。呼び出し側 routes
        が document_figures から用意して渡す。省略時（None）は図埋め込みを解決せず
        原文のまま残す（既存呼び出し元との後方互換）。読み上げ原稿からの図埋め込み除去
        （``strip_figure_embeds``）は本引数の有無に関わらず常に行う（図は読み上げない）。

    Returns
    -------
    tuple[list[dict], str, str, list[dict]]
        ``(slide_dicts, display_text, spoken_text, formulas)``。教材が無ければ
        ``([], "", "", [])``。図メタデータは ``slide_dicts[i]["figures"]``
        （スライド単位で割り当て済み）に含まれる。
    """
    display_text = topic_student_material(topic)
    spoken_text = topic_spoken_script(topic)
    if not display_text and not spoken_text:
        return [], "", "", []

    evidence_links = topic.get("evidence_links") or []
    normalized, formulas = normalize_to_placeholder_format(display_text or spoken_text, [])
    display_text = normalized
    # ![[equation:xxx]] 埋め込みを [[FORMULA_N]] プレースホルダーに解決する
    display_text, formulas = _resolve_equation_embeds(display_text, evidence_links, formulas)
    display_text, formulas = normalize_to_placeholder_format(display_text or spoken_text, formulas)

    # ![[figure:xxx]] 埋め込みを [[FIGURE_N]] プレースホルダーに解決する（Phase 4 §7.2）。
    # 読み上げ原稿には図を含めない（spoken_text からは埋め込みを除去する）。
    display_text, figures = resolve_figure_embeds(display_text, figures_by_id)
    spoken_text = strip_figure_embeds(spoken_text)

    slide_dicts, _mismatch = auto_paginate_slides(display_text, spoken_text, formulas, figures=figures)
    return slide_dicts, display_text, spoken_text, formulas


# ---------------------------------------------------------------------------
# 音声 readiness 判定 (Tier2-11: 講義系の判定共通化)
# ---------------------------------------------------------------------------
#
# 「音声準備完了」の判定はスライド単位 + 言語一致を正本とする（旧: chunk 単位のみの
# 粗い判定と、この slide+language 判定の2実装が並存し、G層 To-Do（旧 chunk 単位）と
# 学習画面のレクチャーボタン活性判定（この関数の旧個別実装）が食い違い得た）。
# ``core/status/projector.py::project_course_status`` と
# ``api/routes/lecture.py::get_topic_audio_status`` の両方から本モジュールの判定を呼ぶ。
# - ``compute_material_audio_readiness``: material_ids に属する chunks の readiness
#   （チャンク経路レクチャー用の下位判定）。
# - ``compute_topic_audio_readiness``: トピック教材経路（``lecture_uses_topic_material``
#   が真のトピック）1件分の readiness（topic_lecture_audio_cache）。
# - ``compute_course_audio_readiness``: コース全体の合算（チャンク経路 + トピック教材
#   経路。N18: トピック draft 充足とトピック音声キャッシュを readiness に反映する正本）。


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


def compute_topic_audio_readiness(
    session,
    course_id: str,
    topic_id: str,
    topic: dict,
    target_language: str,
    voice: str = "alloy",
) -> dict:
    """トピック教材ベースのレクチャー音声の readiness を判定する（唯一の正本）。

    ``build_topic_slides``（受講側・音声生成側と同一のスライド分割＝自動ページ分割込み）で
    導出したスライド数と、``topic_lecture_audio_cache`` にキャッシュ済み（かつ
    ``target_language`` 一致）のスライド数を返す。LLM は呼ばない（軽量なボタン活性判定用）。
    DB 参照に失敗した場合はキャッシュ0件として扱う（fail-safe: 未準備側に倒す）。

    Returns
    -------
    dict
        ``{"total_slides": int, "speakable_slides": int, "ready_slides": int,
        "stale_language": bool}``。``speakable_slides`` は読み上げ原稿（spoken_text）が
        非空のスライド数（＝音声を生成し得るスライド数。draft 充足の指標）。
    """
    slides, _display, _spoken, _formulas = build_topic_slides(topic)

    try:
        rows = session.execute(
            sa_text("""
                SELECT slide_index, language
                FROM topic_lecture_audio_cache
                WHERE course_id = :course_id AND topic_id = :topic_id AND voice = :voice
            """),
            {"course_id": course_id, "topic_id": topic_id, "voice": voice},
        ).fetchall()
    except Exception:
        logger.warning(
            "Failed to compute topic audio readiness for %s/%s", course_id, topic_id, exc_info=True,
        )
        rows = []

    lang_by_slide = {int(r[0]): (r[1] or "ja") for r in rows}
    return _topic_readiness_from_slides(slides, lang_by_slide, target_language)


def _topic_readiness_from_slides(
    slides: list[dict],
    lang_by_slide: dict[int, str],
    target_language: str,
) -> dict:
    """スライド一覧 + キャッシュ済みスライドの言語マップから readiness を数える純関数。"""
    ready_slides = 0
    speakable_slides = 0
    stale_language = False
    for slide in slides:
        if str(slide.get("spoken_text") or "").strip():
            speakable_slides += 1
        cached_language = lang_by_slide.get(slide["slide_index"])
        if cached_language is None:
            continue
        if cached_language == target_language:
            ready_slides += 1
        else:
            stale_language = True
    return {
        "total_slides": len(slides),
        "speakable_slides": speakable_slides,
        "ready_slides": ready_slides,
        "stale_language": stale_language,
    }


def compute_course_audio_readiness(
    session,
    course_id: str,
    course_data: dict,
    target_language: str,
    voice: str = "alloy",
) -> dict:
    """コース全体の音声 readiness をチャンク経路 + トピック教材経路の合算で判定する（唯一の正本）。

    N18: 旧実装（``compute_material_audio_readiness`` 単独）はチャンク経路しか見ておらず、
    トピック教材経路（``lecture_uses_topic_material`` が真のトピック。migration 047）の
    「読み上げ原稿（draft）の充足」と「``topic_lecture_audio_cache`` の生成済みスライド」が
    readiness に反映されなかった（原稿未充足でも audio_status が generated になり得た）。
    本関数はチャンク経路のスライドとトピック教材経路のスライドを同じ土俵（スライド単位 +
    言語一致）で合算する。読み上げ原稿が無い（＝音声を生成し得ない）トピックスライドも
    ``total_slides`` に数える（ready にならない）ことで、draft 未充足が「全スライド生成済み」
    に化けない。``core/status/projector.py::project_course_status`` がこの結果を使う。

    Returns
    -------
    dict
        ``compute_material_audio_readiness`` のキーに加え、内訳として
        ``chunk_total_slides`` / ``chunk_ready_slides`` /
        ``topic_total_topics``（トピック教材経路のトピック数）/ ``topic_total_slides`` /
        ``topic_speakable_slides``（読み上げ原稿が非空のスライド数）/ ``topic_ready_slides`` /
        ``topics_missing_script``（読み上げ可能スライドが1枚も無いトピック数）を返す。
        ``total_slides`` / ``ready_slides`` / ``stale_language`` は両経路の合算値。
    """
    material_ids = course_source_material_ids(course_data)
    chunk_readiness = compute_material_audio_readiness(
        session, material_ids, target_language, voice,
    )

    topic_entries: list[tuple[str, dict]] = []
    for topic in iter_all_topics(course_data):
        if not lecture_uses_topic_material(topic):
            continue
        topic_id = str(topic.get("id") or "").strip()
        if not topic_id:
            continue
        topic_entries.append((topic_id, topic))

    lang_by_topic_slide: dict[str, dict[int, str]] = {}
    if topic_entries:
        # コース分のトピック音声キャッシュを1クエリで取得する（トピック数分の N+1 を避ける）。
        try:
            rows = session.execute(
                sa_text("""
                    SELECT topic_id, slide_index, language
                    FROM topic_lecture_audio_cache
                    WHERE course_id = :course_id AND voice = :voice
                """),
                {"course_id": course_id, "voice": voice},
            ).fetchall()
        except Exception:
            logger.warning(
                "Failed to load topic audio cache for course %s", course_id, exc_info=True,
            )
            rows = []
        for row in rows:
            lang_by_topic_slide.setdefault(str(row[0]), {})[int(row[1])] = row[2] or "ja"

    topic_total_slides = 0
    topic_speakable_slides = 0
    topic_ready_slides = 0
    topics_missing_script = 0
    topic_stale_language = False
    for topic_id, topic in topic_entries:
        slides, _display, _spoken, _formulas = build_topic_slides(topic)
        readiness = _topic_readiness_from_slides(
            slides, lang_by_topic_slide.get(topic_id, {}), target_language,
        )
        topic_total_slides += readiness["total_slides"]
        topic_speakable_slides += readiness["speakable_slides"]
        topic_ready_slides += readiness["ready_slides"]
        topic_stale_language = topic_stale_language or readiness["stale_language"]
        if readiness["total_slides"] > 0 and readiness["speakable_slides"] == 0:
            topics_missing_script += 1

    return {
        # チャンク経路（従来フィールドは透過）
        "total_chunks": chunk_readiness["total_chunks"],
        "generated_chunks": chunk_readiness["generated_chunks"],
        "ready_chunks": chunk_readiness["ready_chunks"],
        "chunk_total_slides": chunk_readiness["total_slides"],
        "chunk_ready_slides": chunk_readiness["ready_slides"],
        # トピック教材経路の内訳
        "topic_total_topics": len(topic_entries),
        "topic_total_slides": topic_total_slides,
        "topic_speakable_slides": topic_speakable_slides,
        "topic_ready_slides": topic_ready_slides,
        "topics_missing_script": topics_missing_script,
        # 合算（呼び出し側の readiness 判定はこの3つを使う）
        "total_slides": chunk_readiness["total_slides"] + topic_total_slides,
        "ready_slides": chunk_readiness["ready_slides"] + topic_ready_slides,
        "stale_language": chunk_readiness["stale_language"] or topic_stale_language,
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
