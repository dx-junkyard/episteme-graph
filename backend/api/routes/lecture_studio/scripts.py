"""Episteme Graph — Lecture Script Studio: 原稿・音声系 (Tier 3-17a パッケージ分割)。

- 口調・読み上げ言語設定 (settings)
- バッチスクリプト生成 / 手動保存 / AI書き換え
- バッチ音声生成 / スライド単位試聴
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

from dependencies import ROLE_SYSTEM_ADMIN, _require_teacher
from schemas import (
    LectureAudioGenerateRequest,
    LectureAudioGenerateStartResponse,
    LectureFormulaItem,
    LecturePreviewSplitRequest,
    LecturePreviewSplitResponse,
    LectureScriptChunkOut,
    LectureScriptGenerateRequest,
    LectureScriptGenerateStartResponse,
    LectureScriptRewriteRequest,
    LectureScriptRewriteResponse,
    LectureScriptSaveRequest,
    LectureScriptSaveResponse,
    LectureSlide,
    LectureStudioSettings,
    LectureTTSResponse,
)
from services import (
    create_background_task,
    get_editable_course_data,
    get_viewable_course_data,
    update_background_task,
)
from core.course_data import lecture_studio_settings as _lecture_studio_settings
from core.lecture import (
    count_slide_marker_segments,
    generate_spoken_text_and_formulas,
    get_course_lecture_language,
    split_slides,
)
from core.llm import generate_text, get_llm_params
from core.llm_usage.context import bind_usage_context, usage_context
from core.personas import course_persona_settings, normalize_persona_id, persona_prompt
from core.postgres import get_session as _pg_session
from core.tts import TtsFatalError, generate_tts_audio

from ._shared import _chunk_status, _get_course_chunks, _get_system_admin_course_data

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Lecture Script Studio"])


def _normalize_lecture_language(value: str | None, default: str = "ja") -> str:
    """読み上げ言語を ``"ja"``/``"en"`` に正規化する（不正値は default にフォールバック）。"""
    return value if value in ("ja", "en") else default


def _save_lecture_studio_settings(course_id: str, course_data: dict, settings: dict[str, str]) -> dict:
    """原稿スタジオのコース単位設定（口調 + 読み上げ言語）を保存する。

    口調 (narration_persona/response_persona) または lecture_language が変わった場合は
    ``scripts_need_regeneration`` を立て、次回のバッチ生成で全チャンクを再生成させる。
    更新後の course_data を返す（呼び出し側が続けて最新設定を使えるように）。
    """
    updated = dict(course_data)
    previous = updated.get("lecture_studio_settings") or {}
    if not isinstance(previous, dict):
        previous = {}
    previous_language = _normalize_lecture_language(previous.get("lecture_language"))
    normalized = {
        "narration_persona": normalize_persona_id(settings.get("narration_persona")),
        "response_persona": normalize_persona_id(settings.get("response_persona")),
        "lecture_language": _normalize_lecture_language(
            settings.get("lecture_language"), default=previous_language,
        ),
    }
    settings_changed = (
        normalize_persona_id(previous.get("narration_persona")) != normalized["narration_persona"]
        or normalize_persona_id(previous.get("response_persona")) != normalized["response_persona"]
        or previous_language != normalized["lecture_language"]
    )
    updated["lecture_studio_settings"] = {
        **normalized,
        "scripts_need_regeneration": bool(previous.get("scripts_need_regeneration")) or settings_changed,
    }
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                UPDATE learning_courses
                SET data = CAST(:data AS jsonb),
                    updated_at = now()
                WHERE id = :course_id
            """),
            {
                "course_id": course_id,
                "data": json.dumps(updated, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return updated


def _save_lecture_language(course_id: str, course_data: dict, language: str) -> dict:
    """コースの ``lecture_language`` のみを更新する（口調設定は保持する）。

    音声生成ダイアログでの言語切替チェーン (§3-3) から呼ばれる。narration/response
    persona は既存値をそのまま引き継ぐため、ここでは persona 側の
    scripts_need_regeneration 判定を再利用して整合させる。
    """
    previous = course_data.get("lecture_studio_settings") or {}
    if not isinstance(previous, dict):
        previous = {}
    return _save_lecture_studio_settings(course_id, course_data, {
        "narration_persona": previous.get("narration_persona"),
        "response_persona": previous.get("response_persona"),
        "lecture_language": language,
    })


def _clear_script_regeneration_flag(course_id: str, course_data: dict) -> None:
    updated = dict(course_data)
    settings = updated.get("lecture_studio_settings") or {}
    if not isinstance(settings, dict):
        return
    if not settings.get("scripts_need_regeneration"):
        return
    updated["lecture_studio_settings"] = {
        **settings,
        "scripts_need_regeneration": False,
    }
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                UPDATE learning_courses
                SET data = CAST(:data AS jsonb),
                    updated_at = now()
                WHERE id = :course_id
            """),
            {
                "course_id": course_id,
                "data": json.dumps(updated, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to clear script regeneration flag for course %s", course_id, exc_info=True)
    finally:
        session.close()


def _normalize_rewrite_result(parsed: object, studio_view: str) -> dict:
    """LLM の rewrite 応答を dict に正規化する。

    LLM が JSON オブジェクトではなくトップレベル配列で返してくることがある。
    studio_view="theory" の場合のみ、その配列を ``theory_components`` として扱う。
    それ以外は空 dict を返してフォールバック値で穴埋めする。
    """
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list) and studio_view == "theory":
        return {"theory_components": parsed}
    return {}


# ---------------------------------------------------------------------------
# 1. バッチスクリプト生成
# ---------------------------------------------------------------------------


@router.get(
    "/courses/{course_id}/lecture-studio/settings",
    response_model=LectureStudioSettings,
)
def get_lecture_studio_settings(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> LectureStudioSettings:
    """原稿スタジオのコース単位設定を取得する。"""
    course_data = get_viewable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")
    lecture_language = _normalize_lecture_language(get_course_lecture_language(course_data))
    return LectureStudioSettings(
        **course_persona_settings(course_data),
        lecture_language=lecture_language,
    )


@router.put(
    "/courses/{course_id}/lecture-studio/settings",
    response_model=LectureStudioSettings,
)
def update_lecture_studio_settings(
    course_id: str,
    body: LectureStudioSettings,
    current_user: dict = Depends(_require_teacher),
) -> LectureStudioSettings:
    """原稿スタジオのコース単位設定を保存する。"""
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    settings = {
        "narration_persona": normalize_persona_id(body.narration_persona),
        "response_persona": normalize_persona_id(body.response_persona),
        "lecture_language": body.lecture_language,
    }
    _save_lecture_studio_settings(course_id, course_data, settings)
    return LectureStudioSettings(**settings)


def _batch_generate_worker(
    task_id: str,
    course_id: str,
    chunks: list[dict],
    override: bool,
    course_data: dict,
    auto_audio: bool = False,
    user_id: str | None = None,
    language: str | None = None,
) -> None:
    """バックグラウンドスレッドでスクリプトを一括生成する。

    auto_audio=True の場合、完了後に音声生成タスクを自動的にキックし、
    結果データの ``next_task_id`` に新タスクIDを格納する (Issue #139)。
    ``language`` 省略時はコース設定 (``lecture_language``) を使う (migration 040 Phase 4)。
    生成した各チャンクには ``chunks.spoken_language`` として生成言語を記録する。
    """
    bind_usage_context("admin:lecture_generate", user_id=user_id, course_id=course_id)
    total = len(chunks)
    generated = 0
    skipped = 0
    settings = course_persona_settings(course_data)
    narration_persona = settings["narration_persona"]
    effective_language = _normalize_lecture_language(
        language, default=get_course_lecture_language(course_data),
    )

    update_background_task(task_id, "processing", result_data={
        "course_id": course_id,
        "phase": "script",
        "total_chunks": total,
        "generated": 0,
        "skipped": 0,
        "progress": 0,
    })

    session = _pg_session()
    try:
        for i, chunk in enumerate(chunks):
            if chunk["spoken_text"] and not override:
                skipped += 1
            else:
                result = generate_spoken_text_and_formulas(
                    chunk_text=chunk["text"],
                    chunk_index=chunk["chunk_index"],
                    course_data=course_data,
                    persona_id=narration_persona,
                    language=effective_language,
                )
                display_text = result.get("display_text") or chunk["text"]
                spoken_text = result["spoken_text"]
                formulas = result["formulas"]

                session.execute(
                    sa_text("""
                        UPDATE chunks
                        SET display_text = :display_text,
                            spoken_text = :spoken_text,
                            formulas = CAST(:formulas AS jsonb),
                            spoken_language = :spoken_language
                        WHERE id = CAST(:id AS uuid)
                    """),
                    {
                        "id": chunk["id"],
                        "display_text": display_text,
                        "spoken_text": spoken_text,
                        "formulas": json.dumps(formulas, ensure_ascii=False),
                        "spoken_language": effective_language,
                    },
                )
                session.commit()
                generated += 1
                # レート制限対策: 生成チャンク間に短い待機を挟む
                time.sleep(1.5)

            # チャンクごとに進捗を更新
            processed = generated + skipped
            update_background_task(task_id, "processing", result_data={
                "course_id": course_id,
                "phase": "script",
                "total_chunks": total,
                "generated": generated,
                "skipped": skipped,
                "progress": int(processed * 100 / total) if total > 0 else 100,
            })

    except Exception as exc:
        session.rollback()
        error_msg = str(exc)
        logger.error("batch_generate_worker failed for task %s: %s", task_id, error_msg)
        update_background_task(task_id, "failed", error_message=error_msg)
        return
    finally:
        session.close()

    # 自動パイプライン: 完了時に音声生成タスクをチェイン (Issue #139)
    next_task_id: str | None = None
    if auto_audio:
        try:
            fresh_chunks = _get_course_chunks(course_data)
            audio_task_id = str(uuid.uuid4())[:12]
            create_background_task(audio_task_id, "audio_generation", user_id)
            threading.Thread(
                target=_batch_audio_worker,
                args=(audio_task_id, course_id, fresh_chunks, effective_language),
                daemon=True,
            ).start()
            next_task_id = audio_task_id
            logger.info(
                "auto_audio chain: script task=%s -> audio task=%s (course=%s)",
                task_id, audio_task_id, course_id,
            )
        except Exception:
            logger.exception("Failed to auto-chain audio task after script task %s", task_id)

    completion_data = {
        "course_id": course_id,
        "phase": "script",
        "total_chunks": total,
        "generated": generated,
        "skipped": skipped,
        "progress": 100,
    }
    if next_task_id:
        completion_data["next_task_id"] = next_task_id
        completion_data["next_task_type"] = "audio_generation"

    update_background_task(task_id, "completed", result_data=completion_data)
    if override:
        _clear_script_regeneration_flag(course_id, course_data)
    logger.info(
        "batch_generate_worker completed: task=%s course=%s generated=%d skipped=%d",
        task_id, course_id, generated, skipped,
    )


@router.post(
    "/courses/{course_id}/lecture-scripts/generate",
    response_model=LectureScriptGenerateStartResponse,
    status_code=202,
)
def batch_generate_scripts(
    course_id: str,
    body: LectureScriptGenerateRequest,
    current_user: dict = Depends(_require_teacher),
) -> LectureScriptGenerateStartResponse:
    """コースの全チャンクに対して spoken_text と formulas を一括生成する（非同期）。

    即座に task_id を返し、処理はバックグラウンドで実行される。
    進捗は GET /api/admin/tasks/{task_id} でポーリングして確認する。
    result_data.progress (0-100) で進捗率を取得できる。
    """
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    chunks = _get_course_chunks(course_data)
    if not chunks:
        raise HTTPException(
            status_code=422,
            detail=(
                "このコースに紐づくテキストチャンクが見つかりません。"
                "教材がコースに設定されているか、またはPDF解析が完了しているかを確認してください。"
            ),
        )

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "script_generation", current_user["id"])
    settings = _lecture_studio_settings(course_data)
    force_regenerate = bool(settings.get("scripts_need_regeneration"))
    effective_override = body.override or force_regenerate

    thread = threading.Thread(
        target=_batch_generate_worker,
        args=(
            task_id,
            course_id,
            chunks,
            effective_override,
            course_data,
            body.auto_audio,
            current_user["id"],
            body.language,
        ),
        daemon=True,
    )
    thread.start()

    logger.info(
        "batch_generate_scripts accepted: task=%s course=%s chunks=%d override=%s auto_audio=%s by user=%s",
        task_id, course_id, len(chunks), effective_override, body.auto_audio, current_user["id"],
    )

    return LectureScriptGenerateStartResponse(
        task_id=task_id,
        course_id=course_id,
        total_chunks=len(chunks),
        status="pending",
    )


# ---------------------------------------------------------------------------
# GET: コースのスクリプト一覧取得
# ---------------------------------------------------------------------------


@router.get(
    "/courses/{course_id}/lecture-scripts",
    response_model=list[LectureScriptChunkOut],
)
def get_course_scripts(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> list[LectureScriptChunkOut]:
    """コースに紐づくチャンクのスクリプト一覧を取得する。

    閲覧権限（オーナー / editor / viewer グループ）で許可する。
    """
    course_data = get_viewable_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    chunks = _get_course_chunks(course_data)
    # スライド単位の音声キャッシュ有無をチャンク数分の N+1 クエリではなく1クエリで取得する。
    audio_slide_map = _load_chunk_slide_audio_map([c["id"] for c in chunks])

    result = []
    for c in chunks:
        slides, mismatch = split_slides(
            c.get("display_text") or c.get("text", ""),
            c.get("spoken_text"),
            c.get("formulas") or [],
        )
        ready_slide_indices = audio_slide_map.get(c["id"], set())
        audio_ready_slides = sum(
            1 for slide in slides if slide["slide_index"] in ready_slide_indices
        )
        result.append(LectureScriptChunkOut(
            chunk_id=c["id"],
            chunk_index=c["chunk_index"],
            text=c["text"],
            raw_text=c.get("raw_text", ""),
            display_text=c.get("display_text", ""),
            spoken_text=c.get("spoken_text", ""),
            formulas=[LectureFormulaItem(**f) for f in c["formulas"]] if c.get("formulas") else [],
            status=_chunk_status(c),
            material_id=c.get("material_id", ""),
            document_id=c.get("document_id", ""),
            page_start=c.get("page_start"),
            page_end=c.get("page_end"),
            section_id=c.get("section_id", ""),
            section_title=c.get("section_title", ""),
            section_level=c.get("section_level", 0),
            section_order=c.get("section_order", 0),
            pdf_url=c.get("pdf_url"),
            smiles_dsl=c.get("smiles_dsl", ""),
            variables=c.get("variables"),
            ancestors=c.get("ancestors"),
            graph_elements=c.get("graph_elements", []),
            spoken_language=c.get("spoken_language") or "ja",
            slide_count=len(slides),
            slide_mismatch=mismatch,
            audio_ready_slides=audio_ready_slides,
        ))
    return result


# ---------------------------------------------------------------------------
# POST: プレビュー用スライド分割（DB 非変更, Tier2-11: 講義系の判定共通化 提案11）
# ---------------------------------------------------------------------------


@router.post(
    "/lecture-studio/preview-split",
    response_model=LecturePreviewSplitResponse,
)
def preview_split_slides(
    body: LecturePreviewSplitRequest,
    current_user: dict = Depends(_require_teacher),
) -> LecturePreviewSplitResponse:
    """原稿スタジオのプレビュー用スライド分割を返す（DB は一切変更しない）。

    ``core.lecture.split_slides`` をそのまま呼ぶことで、プレビュー（本エンドポイント）と
    配信（``get_lecture_sequence`` 等）が同一の分割ロジックを共有する
    （docs/features/lecture_slide_sync_design.md の設計原則）。admin.js の
    `lsSplitSlides` ローカル実装（クライアント側の並行再実装）はこの API 呼び出しに
    置き換え、削除する（Tier2-11）。教員（``_require_teacher``）のみ利用可能。
    """
    slides, mismatch = split_slides(body.display_text, body.spoken_text, body.formulas)
    display_count, spoken_count = count_slide_marker_segments(body.display_text, body.spoken_text)
    return LecturePreviewSplitResponse(
        slides=[
            LectureSlide(
                slide_index=sd["slide_index"],
                display_text=sd["display_text"],
                spoken_text=sd["spoken_text"],
                formulas=[LectureFormulaItem(**f) for f in sd["formulas"]],
            )
            for sd in slides
        ],
        mismatch=mismatch,
        display_segment_count=display_count,
        spoken_segment_count=spoken_count,
    )


def _load_chunk_slide_audio_map(chunk_ids: list[str]) -> dict[str, set[int]]:
    """指定チャンク群のスライド単位音声キャッシュ有無を1クエリで一括取得する。

    Returns
    -------
    dict[str, set[int]]
        ``chunk_id -> {キャッシュ済み slide_index, ...}``（voice='alloy' のみ対象）。
    """
    if not chunk_ids:
        return {}
    session = _pg_session()
    try:
        placeholders = ", ".join(f":cid_{i}" for i in range(len(chunk_ids)))
        params = {f"cid_{i}": cid for i, cid in enumerate(chunk_ids)}
        rows = session.execute(
            sa_text(f"""
                SELECT chunk_id, slide_index
                FROM lecture_audio_cache
                WHERE chunk_id IN ({placeholders}) AND voice = 'alloy'
            """),
            params,
        ).fetchall()
    except Exception:
        logger.warning("Failed to load slide audio map for %d chunks", len(chunk_ids), exc_info=True)
        return {}
    finally:
        session.close()

    out: dict[str, set[int]] = {}
    for row in rows:
        out.setdefault(str(row[0]), set()).add(int(row[1]))
    return out


# ---------------------------------------------------------------------------
# 2. 手動スクリプト保存
# ---------------------------------------------------------------------------


@router.put(
    "/chunks/{chunk_id}/lecture-script",
    response_model=LectureScriptSaveResponse,
)
def save_lecture_script(
    chunk_id: str,
    body: LectureScriptSaveRequest,
    current_user: dict = Depends(_require_teacher),
) -> LectureScriptSaveResponse:
    """教員が編集した spoken_text とメタデータを DB に保存する。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT 1 FROM chunks WHERE id = CAST(:cid AS uuid)"),
            {"cid": chunk_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Chunk not found")

        session.execute(
            sa_text("""
                UPDATE chunks
                SET display_text = :display_text,
                    spoken_text = :spoken_text,
                    formulas = CAST(:formulas AS jsonb)
                WHERE id = CAST(:cid AS uuid)
            """),
            {
                "cid": chunk_id,
                "display_text": body.display_text if body.display_text is not None else body.spoken_text,
                "spoken_text": body.spoken_text,
                "formulas": json.dumps(body.formulas, ensure_ascii=False),
            },
        )

        # 音声キャッシュを無効化（スクリプトが変更されたため）
        session.execute(
            sa_text("DELETE FROM lecture_audio_cache WHERE chunk_id = CAST(:cid AS uuid)"),
            {"cid": chunk_id},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to save lecture script for chunk %s", chunk_id)
        raise HTTPException(status_code=500, detail="Failed to save script")
    finally:
        session.close()

    return LectureScriptSaveResponse(chunk_id=chunk_id, status="edited")


# ---------------------------------------------------------------------------
# 3. AI スクリプト書き換え
# ---------------------------------------------------------------------------

_REWRITE_PROMPT = """あなたは大学講義の音声原稿を改善するアシスタントです。

以下のソーステキストと現在の表示テキスト・音声読み上げ原稿、そして教員からの指示に基づいて、
画面表示テキストと音声原稿を書き換えてください。

**重要:**
- 教員の指示に従い、必要に応じて一般的な物理学・数学の知識を補足してください
- ソーステキストに限定されず、教員が指示する内容を反映させてください
- display_text では数式を `[[FORMULA_0]]`, `[[FORMULA_1]]` のようなプレースホルダーで表現してください。`$...$` や `$$...$$` は使わないでください
- spoken_text では LaTeX 数式を自然言語に変換してください（例: `E = mc^2` → 「Eイコールmcの二乗」）
- ソーステキストが日本語の場合、自然な日本語の講義調で書いてください。
- ソーステキストが英語の場合、無理にカタカナや全角に変換せず、**自然な半角英語の文章（Natural English sentences）**として書いてください
- 数式メタデータも更新してください

## ソーステキスト:
{source_text}

## 現在の表示テキスト:
{current_display_text}

## 現在の音声原稿:
{current_spoken_text}

## 教員からの指示:
{instructor_prompt}

## 読み上げテキストの語り口設定:
{persona_instruction}

## 出力形式 (厳密にJSON):
{{
  "display_text": "エネルギーは [[FORMULA_0]] で表される。",
  "spoken_text": "エネルギーは Eイコールmcの二乗 で表される。",
  "formulas": [
    {{"id": "[[FORMULA_0]]", "latex": "E = mc^2", "spoken": "Eイコールmcの二乗", "is_display": false}}
  ]
}}

重要: JSON のみを出力してください。マークダウンコードフェンスは不要です。"""

_THEORY_ASSIST_PROMPT = """あなたは原稿スタジオの理論コンポーネント編集アシスタントです。

現在のタブ: 理論

目的:
- 既存コンポーネントの inputs / outputs は維持してください。
- summary, preconditions, constraints, invalid_conditions, dependencies, teacher_notes を改善してください。
- ソース本文・DSL・既存JSONを優先してください。
- 一般的な素粒子物理学・場の理論・有効理論の知識で妥当に補える場合は補ってください。
- 一般知識で補った項目は needs_source: true, source_refs: [] にしてください。
- 推測にしかならない場合、label は「未確定」にしてください。
- 「DSLから生成した候補」のような実装説明は summary に入れないでください。
- 出力JSONには inputs と outputs を含めないでください。
- JSONのみを出力してください。

ソース本文:
{source_text}

表示テキスト:
{current_display_text}

構造情報:
smiles_dsl:
{smiles_dsl}

variables:
{variables}

ancestors:
{ancestors}

現在の理論コンポーネントJSON:
{theory_components}

教員からの指示:
{instructor_prompt}

出力JSON:
{{
  "theory_components": [
    {{
      "id": "既存ID",
      "name": "既存名",
      "component_type": "theory",
      "summary": "",
      "status": "candidate",
      "source_chunks": [],
      "preconditions": [],
      "constraints": [],
      "invalid_conditions": [],
      "dependencies": [],
      "blackbox_policy": {{"default_level": "summary", "expand_if_unlearned": true}},
      "teacher_notes": ""
    }}
  ]
}}
"""

_AUDIO_ASSIST_PROMPT = """あなたは大学講義の読み上げ原稿を改善するアシスタントです。

現在のタブ: 音声

目的:
- 表示テキストや数式プレースホルダーは変更せず、spoken_text だけを改善してください。
- 音声で自然に理解できる文にしてください。
- 数式・記号は必要に応じて自然な読みへ変換してください。
- JSONのみを出力してください。

ソース本文:
{source_text}

表示テキスト:
{current_display_text}

現在の読み上げテキスト:
{current_spoken_text}

教員からの指示:
{instructor_prompt}

読み上げテキストの語り口設定:
{persona_instruction}

出力JSON:
{{
  "display_text": "{current_display_text}",
  "spoken_text": "",
  "formulas": []
}}
"""

_DISPLAY_ASSIST_PROMPT = """あなたは原稿スタジオの表示テキスト・数式編集アシスタントです。

現在のタブ: {studio_view}

目的:
- 表示テキストと数式メタデータを改善してください。
- display_text では数式を [[FORMULA_0]], [[FORMULA_1]] のようなプレースホルダーで表現してください。
- formulas には各プレースホルダーの latex / spoken / is_display を入れてください。
- spoken_text は表示テキストに対応する自然な読み上げ文にしてください。
- JSONのみを出力してください。

ソース本文:
{source_text}

現在の表示テキスト:
{current_display_text}

現在の読み上げテキスト:
{current_spoken_text}

現在の数式:
{current_formulas}

教員からの指示:
{instructor_prompt}

読み上げテキストの語り口設定:
{persona_instruction}

出力JSON:
{{
  "display_text": "",
  "spoken_text": "",
  "formulas": []
}}
"""


@router.post(
    "/chunks/{chunk_id}/lecture-script/rewrite",
    response_model=LectureScriptRewriteResponse,
)
def rewrite_lecture_script(
    chunk_id: str,
    body: LectureScriptRewriteRequest,
    current_user: dict = Depends(_require_teacher),
) -> LectureScriptRewriteResponse:
    """教員の指示に基づいて AI でスクリプトを書き換える。

    ソーステキストに限定せず、教員の指示に従い一般知識も活用して書き換える。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT text, display_text, spoken_text, formulas, smiles_dsl, variables, ancestors
                FROM chunks
                WHERE id = CAST(:cid AS uuid)
            """),
            {"cid": chunk_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Chunk not found")

        source_text = row[0] or ""
        current_display = row[1] or source_text
        current_spoken = row[2] or source_text
        current_formulas = row[3] if row[3] else []
        smiles_dsl = row[4] or ""
        variables = row[5] if row[5] is not None else None
        ancestors = row[6] if row[6] is not None else None
    finally:
        session.close()

    studio_view = (body.studio_view or "edit").strip().lower()
    persona_instruction = persona_prompt(body.narration_persona, target="narration") or "指定なし。通常の自然な講義調で書き換えてください。"
    if studio_view == "theory":
        prompt = _THEORY_ASSIST_PROMPT.format(
            source_text=source_text[:4000],
            current_display_text=current_display[:4000],
            smiles_dsl=smiles_dsl[:3000],
            variables=json.dumps(variables, ensure_ascii=False)[:3000],
            ancestors=json.dumps(ancestors, ensure_ascii=False)[:3000],
            theory_components=json.dumps(body.theory_components, ensure_ascii=False, indent=2)[:8000],
            instructor_prompt=body.prompt[:2000],
        )
    elif studio_view == "audio":
        prompt = _AUDIO_ASSIST_PROMPT.format(
            source_text=source_text[:4000],
            current_display_text=current_display[:4000],
            current_spoken_text=current_spoken[:4000],
            instructor_prompt=body.prompt[:2000],
            persona_instruction=persona_instruction,
        )
    elif studio_view in ("compare", "edit"):
        prompt = _DISPLAY_ASSIST_PROMPT.format(
            studio_view=studio_view,
            source_text=source_text[:4000],
            current_display_text=current_display[:4000],
            current_spoken_text=current_spoken[:4000],
            current_formulas=json.dumps(current_formulas, ensure_ascii=False)[:3000],
            instructor_prompt=body.prompt[:2000],
            persona_instruction=persona_instruction,
        )
    else:
        prompt = _REWRITE_PROMPT.format(
            source_text=source_text[:4000],
            current_display_text=current_display[:4000],
            current_spoken_text=current_spoken[:4000],
            instructor_prompt=body.prompt[:2000],
            persona_instruction=persona_instruction,
        )

    params = get_llm_params("fast")

    try:
        with usage_context("admin:lecture_rewrite", user_id=current_user["id"]):
            raw = generate_text(
                messages=[{"role": "user", "content": prompt}],
                model=params["model"],
                reasoning_effort=params["reasoning_effort"],
            )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            cleaned = "\n".join(lines)
        result = _normalize_rewrite_result(json.loads(cleaned, strict=False), studio_view)
        theory_components = result.get("theory_components", [])
        display_text = result.get("display_text") or current_display
        spoken_text = result.get("spoken_text", current_spoken)
        if studio_view == "theory":
            display_text = current_display
            spoken_text = current_spoken
            formulas = current_formulas
        elif studio_view == "audio":
            display_text = current_display
            formulas = current_formulas
        else:
            formulas = result.get("formulas", [])
    except Exception:
        logger.exception("AI rewrite failed for chunk %s", chunk_id)
        raise HTTPException(status_code=500, detail="AI rewrite failed")

    if studio_view != "theory":
        session = _pg_session()
        try:
            session.execute(
                sa_text("""
                    UPDATE chunks
                    SET display_text = :display_text,
                        spoken_text = :spoken_text,
                        formulas = CAST(:formulas AS jsonb)
                    WHERE id = CAST(:cid AS uuid)
                """),
                {
                    "cid": chunk_id,
                    "display_text": display_text,
                    "spoken_text": spoken_text,
                    "formulas": json.dumps(formulas, ensure_ascii=False),
                },
            )
            # 音声キャッシュを無効化
            session.execute(
                sa_text("DELETE FROM lecture_audio_cache WHERE chunk_id = CAST(:cid AS uuid)"),
                {"cid": chunk_id},
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return LectureScriptRewriteResponse(
        chunk_id=chunk_id,
        display_text=display_text,
        spoken_text=spoken_text,
        formulas=[LectureFormulaItem(**f) for f in formulas],
        theory_components=theory_components if isinstance(theory_components, list) else [],
    )


# ---------------------------------------------------------------------------
# 4. バッチ音声生成
# ---------------------------------------------------------------------------


def _batch_audio_worker(
    task_id: str,
    course_id: str,
    chunks: list[dict],
    course_language: str = "ja",
) -> None:
    """バックグラウンドスレッドでスライド単位に TTS 音声を一括生成する (migration 040)。

    各チャンクを ``split_slides`` でスライドに分割し、スライドごとに
    ``lecture_audio_cache (chunk_id, slide_index, voice)`` へキャッシュする。
    進捗（generated/skipped/errors/progress）は従来どおりチャンク単位で集計する
    （1チャンク内で1枚でも生成できれば generated、全スライドスキップなら skipped、
    生成0件でエラーのみなら errors）。

    ``course_language`` はチャンク自身に ``spoken_language`` が無い場合のフォールバック
    （通常は原稿生成時に書き込まれているため、この引数はコース設定からの保険値）。
    言語切替チェーン (``_batch_generate_and_audio_worker``) から呼ばれる場合はこの
    タスク自体が既に ``phase: "script"`` を経ているため、ここでは ``phase: "audio"`` を
    result_data に出す (§3-3)。
    """
    bind_usage_context("admin:lecture_tts", course_id=course_id)
    total = len(chunks)
    generated = 0
    skipped = 0
    errors = 0

    update_background_task(task_id, "processing", result_data={
        "course_id": course_id,
        "phase": "audio",
        "total_chunks": total,
        "generated": 0,
        "skipped": 0,
        "errors": 0,
        "progress": 0,
    })

    for chunk in chunks:
        chunk_id = chunk["id"]
        spoken_text = chunk.get("spoken_text")

        if not spoken_text:
            skipped += 1
            processed = generated + skipped + errors
            update_background_task(task_id, "processing", result_data={
                "course_id": course_id,
                "phase": "audio",
                "total_chunks": total,
                "generated": generated,
                "skipped": skipped,
                "errors": errors,
                "progress": int(processed * 100 / total) if total > 0 else 100,
            })
            continue

        display_text = chunk.get("display_text") or chunk.get("text") or ""
        formulas = chunk.get("formulas") or []
        chunk_language = chunk.get("spoken_language") or course_language
        slides, _mismatch = split_slides(display_text, spoken_text, formulas)

        # 分割数が減った場合の残留スライド行を掃除する（生成前に一度だけ）
        session = _pg_session()
        try:
            session.execute(
                sa_text("""
                    DELETE FROM lecture_audio_cache
                    WHERE chunk_id = CAST(:cid AS uuid) AND slide_index >= :slide_count
                """),
                {"cid": chunk_id, "slide_count": len(slides)},
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.warning(
                "Failed to clean up stale slide audio rows for chunk %s", chunk_id, exc_info=True,
            )
        finally:
            session.close()

        chunk_generated = 0
        chunk_errors = 0
        aborted = False

        for slide in slides:
            slide_spoken = slide.get("spoken_text")
            slide_index = slide["slide_index"]
            if not slide_spoken:
                # spoken_text の無いスライド（従来のチャンク単位スキップと同義）
                continue

            # スライド単位のキャッシュ確認
            session = _pg_session()
            try:
                cached = session.execute(
                    sa_text("""
                        SELECT 1 FROM lecture_audio_cache
                        WHERE chunk_id = CAST(:cid AS uuid) AND slide_index = :slide_index AND voice = :voice
                        LIMIT 1
                    """),
                    {"cid": chunk_id, "slide_index": slide_index, "voice": "alloy"},
                ).fetchone()
            finally:
                session.close()

            if cached:
                continue

            try:
                # TTS 生成（プロバイダは generate_tts_audio が自動選択。言語はチャンクの
                # spoken_language を最優先し、無ければコース設定にフォールバックする）
                audio_bytes = generate_tts_audio(slide_spoken, language=chunk_language)
                if audio_bytes is None:
                    chunk_errors += 1
                    logger.warning(
                        "TTS audio generation returned None for chunk %s slide %d (no provider available)",
                        chunk_id, slide_index,
                    )
                    continue

                duration_ms = max(1000, len(audio_bytes) * 8 // 128)

                session = _pg_session()
                try:
                    session.execute(
                        sa_text("""
                            INSERT INTO lecture_audio_cache
                                (chunk_id, slide_index, voice, audio_data, duration_ms, language)
                            VALUES
                                (CAST(:cid AS uuid), :slide_index, :voice, :audio_data, :duration_ms, :language)
                            ON CONFLICT (chunk_id, slide_index, voice) DO UPDATE
                            SET audio_data = EXCLUDED.audio_data,
                                duration_ms = EXCLUDED.duration_ms,
                                language = EXCLUDED.language,
                                created_at = now()
                        """),
                        {
                            "cid": chunk_id,
                            "slide_index": slide_index,
                            "voice": "alloy",
                            "audio_data": audio_bytes,
                            "duration_ms": duration_ms,
                            "language": chunk_language,
                        },
                    )
                    session.commit()
                    chunk_generated += 1
                except Exception:
                    session.rollback()
                    chunk_errors += 1
                    logger.warning(
                        "Failed to cache audio for chunk %s slide %d", chunk_id, slide_index, exc_info=True,
                    )
                finally:
                    session.close()

                # レート制限対策: スライド間に 0.5 秒の遅延
                time.sleep(0.5)
            except TtsFatalError as exc:
                # API 未有効化・認証エラーなど恒久的な失敗: 残りを処理しても無駄なので即終了
                error_msg = str(exc)
                logger.error("TTS fatal error, aborting task %s: %s", task_id, error_msg)
                update_background_task(task_id, "failed", error_message=error_msg)
                aborted = True
                break
            except Exception:
                chunk_errors += 1
                logger.warning(
                    "TTS generation failed for chunk %s slide %d", chunk_id, slide_index, exc_info=True,
                )

        if aborted:
            return

        # チャンク単位の集計（進捗率は従来どおりチャンク数ベース）
        if chunk_generated > 0:
            generated += 1
        elif chunk_errors > 0:
            errors += 1
        else:
            skipped += 1

        processed = generated + skipped + errors
        update_background_task(task_id, "processing", result_data={
            "course_id": course_id,
            "phase": "audio",
            "total_chunks": total,
            "generated": generated,
            "skipped": skipped,
            "errors": errors,
            "progress": int(processed * 100 / total) if total > 0 else 100,
        })

    update_background_task(task_id, "completed", result_data={
        "course_id": course_id,
        "phase": "audio",
        "total_chunks": total,
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
        "progress": 100,
    })
    logger.info(
        "batch_audio_worker completed: task=%s course=%s generated=%d skipped=%d errors=%d",
        task_id, course_id, generated, skipped, errors,
    )


def _batch_generate_and_audio_worker(
    task_id: str,
    course_id: str,
    course_data: dict,
    target_language: str,
) -> None:
    """言語切替時の連鎖ワーカー: 原稿再生成 (phase=script) → 音声生成 (phase=audio) を
    1つの background task 内でフェーズ進行させる (§3-3)。

    設計上の判断（報告参照）: display_text は言語別に温存せず、``_batch_generate_worker``
    と同じ override 全再生成経路を再利用する（display_text はソース言語のまま生成される
    ため実害は無く、実装を単純に保てる）。各チャンク更新のたびに当該チャンクの音声
    キャッシュ（旧言語含む全スライド）を削除し、既存の「原稿変更時は音声キャッシュ無効化」
    ルールと同じ挙動にする。
    """
    bind_usage_context("admin:lecture_generate", course_id=course_id)
    chunks = _get_course_chunks(course_data)
    total = len(chunks)
    settings = course_persona_settings(course_data)
    narration_persona = settings["narration_persona"]

    update_background_task(task_id, "processing", result_data={
        "course_id": course_id,
        "phase": "script",
        "total_chunks": total,
        "generated": 0,
        "skipped": 0,
        "progress": 0,
    })

    generated = 0
    session = _pg_session()
    try:
        for chunk in chunks:
            result = generate_spoken_text_and_formulas(
                chunk_text=chunk["text"],
                chunk_index=chunk["chunk_index"],
                course_data=course_data,
                persona_id=narration_persona,
                language=target_language,
            )
            display_text = result.get("display_text") or chunk["text"]
            spoken_text = result["spoken_text"]
            formulas = result["formulas"]

            session.execute(
                sa_text("""
                    UPDATE chunks
                    SET display_text = :display_text,
                        spoken_text = :spoken_text,
                        formulas = CAST(:formulas AS jsonb),
                        spoken_language = :spoken_language
                    WHERE id = CAST(:id AS uuid)
                """),
                {
                    "id": chunk["id"],
                    "display_text": display_text,
                    "spoken_text": spoken_text,
                    "formulas": json.dumps(formulas, ensure_ascii=False),
                    "spoken_language": target_language,
                },
            )
            # 言語が変わったため旧言語の音声キャッシュ（当該チャンクの全スライド）を無効化する
            # （既存の「原稿変更時は音声キャッシュ無効化」ルールと同じ挙動。§2-3）
            session.execute(
                sa_text("DELETE FROM lecture_audio_cache WHERE chunk_id = CAST(:cid AS uuid)"),
                {"cid": chunk["id"]},
            )
            session.commit()
            generated += 1
            update_background_task(task_id, "processing", result_data={
                "course_id": course_id,
                "phase": "script",
                "total_chunks": total,
                "generated": generated,
                "skipped": 0,
                "progress": int(generated * 100 / total) if total > 0 else 100,
            })
            time.sleep(1.5)
    except Exception as exc:
        session.rollback()
        error_msg = str(exc)
        logger.error(
            "batch_generate_and_audio_worker (script phase) failed for task %s: %s",
            task_id, error_msg,
        )
        update_background_task(task_id, "failed", error_message=error_msg)
        return
    finally:
        session.close()

    _clear_script_regeneration_flag(course_id, course_data)

    # phase 2: 音声生成（更新済みの spoken_text / spoken_language で再取得する）
    fresh_chunks = _get_course_chunks(course_data)
    logger.info(
        "language switch chain: script phase done (task=%s course=%s language=%s), "
        "starting audio phase",
        task_id, course_id, target_language,
    )
    _batch_audio_worker(task_id, course_id, fresh_chunks, target_language)


@router.post(
    "/courses/{course_id}/lecture-audio/generate",
    response_model=LectureAudioGenerateStartResponse,
    status_code=202,
)
def batch_generate_audio(
    course_id: str,
    body: LectureAudioGenerateRequest | None = None,
    current_user: dict = Depends(_require_teacher),
) -> LectureAudioGenerateStartResponse:
    """コースの全スクリプトに対して TTS 音声を一括生成する（非同期）。

    即座に task_id を返し、処理はバックグラウンドで実行される。
    進捗は GET /api/admin/tasks/{task_id} でポーリングして確認する。
    result_data.progress (0-100) で進捗率を取得できる（言語切替時は result_data.phase が
    "script" → "audio" と遷移する。§3-3）。

    ``body.language`` がコース設定の ``lecture_language`` と異なる場合、またはいずれかの
    チャンクの ``spoken_language`` が指定言語と食い違う場合は、原稿再生成 → 音声生成の
    チェーンを1タスクで実行する（既存の音声は無効化されることが前提。§3-3 の確認ダイアログは
    フロント側の責務）。同一言語で全チャンクの spoken_language も一致していれば、
    従来どおり未生成スライドのみ生成する。

    音声準備確認フロー (Issue #491) §4: UI を経由しない呼び出しに対しても前提を強制する。
    対象チャンクが無ければ 422、1件でも読み上げ原稿 (``spoken_text``) が空の対象チャンクが
    あれば不足件数付きで 422 を返し、ワーカーが空原稿を黙ってスキップして見かけ上成功する
    経路を残さない。ただし言語切替チェーン（原稿再生成 → 音声生成）は例外で、原稿再生成後に
    全対象の ``spoken_text`` を作り直してから音声生成するため、この事前チェックを免除する。
    """
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    current_language = _normalize_lecture_language(get_course_lecture_language(course_data))
    requested_language = body.language if body else None
    target_language = _normalize_lecture_language(requested_language, default=current_language)

    chunks = _get_course_chunks(course_data)
    if not chunks:
        raise HTTPException(
            status_code=422,
            detail=(
                "このコースに紐づくテキストチャンクが見つかりません。"
                "教材がコースに設定されているか、またはPDF解析が完了しているかを確認してください。"
            ),
        )

    language_mismatch = any(
        _normalize_lecture_language(c.get("spoken_language"), default=current_language) != target_language
        for c in chunks
    )
    needs_chain = target_language != current_language or language_mismatch

    # 音声準備確認フロー (Issue #491) §4: 言語切替チェーンでない（＝既存の spoken_text を
    # そのまま音声化する）通常経路では、全対象チャンクに空でない読み上げ原稿があることを
    # 要求する。``stored_spoken_text`` は DB 実値（``_get_course_chunks`` の ``spoken_text``
    # は display_text へフォールバックするため readiness 判定には使わない）。
    if not needs_chain:
        missing = sum(1 for c in chunks if not str(c.get("stored_spoken_text") or "").strip())
        if missing:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"読み上げ原稿が未生成の箇所が {missing} 件あります。"
                    "先に読み上げ原稿を生成してから音声を生成してください。"
                ),
            )

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "audio_generation", current_user["id"])

    if needs_chain:
        if target_language != current_language:
            course_data = _save_lecture_language(course_id, course_data, target_language)
        thread = threading.Thread(
            target=_batch_generate_and_audio_worker,
            args=(task_id, course_id, course_data, target_language),
            daemon=True,
        )
    else:
        thread = threading.Thread(
            target=_batch_audio_worker,
            args=(task_id, course_id, chunks, target_language),
            daemon=True,
        )
    thread.start()

    logger.info(
        "batch_generate_audio accepted: task=%s course=%s chunks=%d language=%s chain=%s by user=%s",
        task_id, course_id, len(chunks), target_language, needs_chain, current_user["id"],
    )

    return LectureAudioGenerateStartResponse(
        task_id=task_id,
        course_id=course_id,
        total_chunks=len(chunks),
        status="pending",
    )


# ---------------------------------------------------------------------------
# 4b. スライド単位の試聴 (migration 040 Phase 4 §4-2)
# ---------------------------------------------------------------------------


@router.get(
    "/chunks/{chunk_id}/lecture-audio",
    response_model=LectureTTSResponse,
)
def preview_lecture_audio(
    chunk_id: str,
    slide_index: int = 0,
    voice: str = "alloy",
    current_user: dict = Depends(_require_teacher),
) -> LectureTTSResponse:
    """教員が原稿スタジオでスライド単位の生成済み音声を試聴する。

    受講画面と同じキャッシュキー (chunk_id, slide_index, voice) で
    ``lecture_audio_cache`` を引く。**キャッシュ配信のみ・生成は行わない**方針は学習者向け
    ``/tts`` と同じ（未生成の場合は 404）。認可は本ファイルの他のチャンク単位エンドポイント
    （``save_lecture_script`` 等）と同じパターン（``_require_teacher`` のみ、コース単位の
    追加チェックはしない）を踏襲する。
    """
    try:
        uuid.UUID(chunk_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=404,
            detail="この内容の音声はまだ生成されていません。",
        )

    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT audio_data, duration_ms, word_timestamps
                FROM lecture_audio_cache
                WHERE chunk_id = CAST(:cid AS uuid) AND voice = :voice AND slide_index = :slide_index
                LIMIT 1
            """),
            {"cid": chunk_id, "voice": voice, "slide_index": slide_index},
        ).fetchone()
    except Exception:
        # DB エラーでも 500 で詳細を漏らさず「未生成」扱いにする（学習者向け
        # _get_audio_cache と同じ fail-safe な方針）。
        logger.warning(
            "Failed to look up preview audio cache for chunk %s slide %d", chunk_id, slide_index,
            exc_info=True,
        )
        raise HTTPException(
            status_code=404,
            detail="この内容の音声はまだ生成されていません。",
        )
    finally:
        session.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="この内容の音声はまだ生成されていません。",
        )

    audio_bytes = row[0]
    if isinstance(audio_bytes, memoryview):
        audio_bytes = bytes(audio_bytes)

    return LectureTTSResponse(
        chunk_id=chunk_id,
        audio_base64=base64.b64encode(audio_bytes).decode("utf-8"),
        duration_ms=row[1] or 0,
        word_timestamps=row[2] if row[2] else [],
    )
