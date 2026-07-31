"""Episteme Graph — Lecture Script Studio: 原稿スタジオ左ペイン系 (Tier 3-17a パッケージ分割)。

- コース章・トピック構造取得 (course-structure)
- コーストピックの授業用ドラフト保存・AI書き換え
- 文書構造・理論コンポーネント一覧取得
"""

from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from dependencies import ROLE_SYSTEM_ADMIN, _require_teacher
from services import get_viewable_course_data
from core.course_data import (
    course_chapters,
    course_source_material_ids,
    course_sources,
    course_title as _course_title,
    course_topics,
    find_course_topic,
)
from core import llm_policy
from core.course_content_builder import (
    _ensure_required_figures_in_material,
    _required_figure_items,
)
from core.llm import generate_text, generate_text_with_structured_output, get_llm_params
from core.llm_usage.context import usage_context
from core.postgres import get_session as _pg_session
from core import element_explanations
# 教材図スタジオ（teaching_figure_studio_design.md §7.3）: 原稿スタジオプレビューの
# 図解決マップ。学習画面（``_load_course_figures_by_id``）と同じ解決規則を共有する。
from routes.lecture import load_course_figures_index_for_admin

from ._shared import (
    _chunk_status,
    _course_data_for_studio_editable as _shared_course_data_for_studio_editable,
    _get_course_chunks,
    _get_system_admin_course_data,
    consume_lecture_rewrite_quota,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Lecture Script Studio"])


# ---------------------------------------------------------------------------
# 7. 原稿スタジオ — 左ペイン用 3系統データ取得 (Issue #232)
# ---------------------------------------------------------------------------


def _course_data_for_studio(course_id: str, current_user: dict) -> dict:
    """閲覧権限チェック付きでコースデータを返す。"""
    course_data = get_viewable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")
    return course_data


def _course_data_for_studio_editable(course_id: str, current_user: dict) -> dict:
    """編集権限チェック付きでコースデータを返す。

    正本は ``_shared.py``（教材図スタジオ設計書 §8 で共有化。本モジュールは委譲に
    置き換え、``routes.lecture_studio.topics._course_data_for_studio_editable`` という
    参照名と挙動は不変に保つ）。
    """
    return _shared_course_data_for_studio_editable(course_id, current_user)


@router.get("/courses/{course_id}/lecture-studio/course-structure")
def get_lecture_studio_course_structure(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コース章・トピック構造を返す。

    ``learning_courses.data.chapters`` / ``data.topics`` を使い、
    各トピックのスクリプトステータスをチャンク情報から算出する。
    """
    course_data = _course_data_for_studio(course_id, current_user)

    # --- コースタイトル ---
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT title FROM learning_courses WHERE id = :cid LIMIT 1"),
            {"cid": course_id},
        ).fetchone()
    finally:
        session.close()
    course_title = (row[0] if row else None) or _course_title(course_data)

    # --- チャンクのステータス集計 ---
    chunks = _get_course_chunks(course_data)
    total_chunks = len(chunks)
    generated_chunks = sum(1 for c in chunks if c.get("stored_spoken_text"))
    audio_chunks = 0
    if chunks:
        chunk_ids = [c["id"] for c in chunks]
        placeholders = ", ".join(f":cid_{i}" for i in range(len(chunk_ids)))
        params: dict = {f"cid_{i}": cid for i, cid in enumerate(chunk_ids)}
        session = _pg_session()
        try:
            rows = session.execute(
                sa_text(f"SELECT COUNT(*) FROM lecture_audio_cache WHERE chunk_id IN (SELECT unnest(ARRAY[{placeholders}]::uuid[]))"),
                params,
            ).fetchone()
            audio_chunks = rows[0] if rows else 0
        except Exception:
            pass
        finally:
            session.close()

    # --- コース全体ステータス ---
    if total_chunks == 0:
        course_status = "no_chunks"
    elif audio_chunks == total_chunks:
        course_status = "audio_generated"
    elif generated_chunks == total_chunks:
        course_status = "generated"
    elif generated_chunks > 0:
        course_status = "partial"
    else:
        course_status = "draft"

    # --- 章・トピック構造を構築 ---
    chapters_raw = course_chapters(course_data)
    topics_raw = course_topics(course_data)

    # topics_raw が chapter_index でグループ化されている形式に対応
    chapter_topics: dict[int, list] = {}
    for t in topics_raw:
        ci = t.get("chapter_index", 0)
        chapter_topics.setdefault(ci, []).append(t)

    chapters_out = []
    for ci, ch in enumerate(chapters_raw):
        topics_in_chapter = chapter_topics.get(ci, ch.get("topics", []))
        topics_out = []
        for ti, t in enumerate(topics_in_chapter):
            prereqs = []
            for p in t.get("prerequisites", []):
                name = p.get("name", "") if isinstance(p, dict) else str(p)
                if name:
                    prereqs.append(name)
            topics_out.append({
                "id": t.get("id", f"topic_{ci}_{ti}"),
                "topic_index": ti,
                "title": t.get("title", ""),
                "key_concepts": t.get("key_concepts", []),
                "student_material": t.get("student_material", {}),
                "spoken_script": t.get("spoken_script", ""),
                "cautions": t.get("cautions", []),
                "check_questions": t.get("check_questions", []),
                "evidence_links": t.get("evidence_links", []),
                "coverage": t.get("coverage", {}),
                "prerequisites": prereqs,
                "summary": t.get("summary", ""),
                "content": t.get("content", ""),
                "content_blocks": t.get("content_blocks", []),
                "content_source": t.get("content_source", ""),
                "content_confidence": t.get("content_confidence", ""),
                "linked_component_ids": t.get("linked_component_ids", []),
                "linked_equation_ids": t.get("linked_equation_ids", []),
                "source_evidence_ids": t.get("source_evidence_ids", []),
                "linked_chunk_ids": t.get("linked_chunk_ids", []) or t.get("material_chunk_ids", []),
                "status": "generated" if (t.get("content") or t.get("summary")) else "draft",
            })
        chapters_out.append({
            "chapter_index": ci,
            "title": ch.get("title", ""),
            "topics": topics_out,
        })

    # 図マップ（教材図スタジオ設計書 §7.3）: 学習画面と同じ解決規則で
    # figure_id -> {caption, image_url(admin 経路), document_id, teaching} を返す。
    # これにより studio プレビューが evidence_links 由来の図だけでなく、教員が本文に
    # 手で書いた任意の figure_id も画像表示できる（学習画面との非対称の解消）。
    # 取得失敗はプレビューの劣化に留める（構造取得自体は成功させる・fail-soft）。
    try:
        figures_index = load_course_figures_index_for_admin(course_id, course_data)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to build figures_index for course %s", course_id, exc_info=True)
        figures_index = {}

    return {
        "course_id": course_id,
        "title": course_title,
        "course_status": course_status,
        "course_content_status": course_data.get("course_content_status") or {},
        "total_chunks": total_chunks,
        "generated_chunks": generated_chunks,
        "audio_chunks": audio_chunks,
        "chapters": chapters_out,
        "figures_index": figures_index,
    }


def _find_course_topic(course_data: dict, topic_id: str, chapter_index: object = None, topic_index: object = None) -> dict | None:
    """正本は ``core/course_data.py::find_course_topic``（Tier3-18 で委譲に変更）。"""
    return find_course_topic(course_data, topic_id, chapter_index, topic_index)


def _clean_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return []


def _topic_figures_for_prompt(topic: dict) -> list[dict]:
    """トピックの evidence_links から kind='figure' を抜き出し、LLM が
    `![[figure:<figure_id>]]` を実 ID で埋め込めるようにする（Phase 4 §7.1）。

    Phase 2 §5.3 続き: 承認済み(approved)/候補(candidate) の contextual
    element_explanations があれば ``explanation`` / ``explanation_status``
    （``"approved"`` | ``"ai_candidate"``）を添える。教員向け下書きの根拠材料のため、
    未確定の candidate も「AI候補」であることが分かるラベル付きで供給してよい
    （学習者向け descriptor / チャット説明とは異なり candidate-only 原則の例外——
    ここでの読者は必ず教員であり、最終確認は教員が下書きをレビューする際に行う）。
    approved を candidate より優先する。element_explanations が無い/未取得の場合は
    従来どおり ``{figure_id, caption}`` のみ返す（情報を落とさない・fail-soft）。
    """
    figures: list[dict] = []
    seen: set[str] = set()
    figure_document_ids: dict[str, str] = {}
    for link in topic.get("evidence_links") or []:
        if not isinstance(link, dict) or link.get("kind") != "figure":
            continue
        figure_id = str(link.get("figure_id") or link.get("target_id") or "").strip()
        if not figure_id or figure_id in seen:
            continue
        seen.add(figure_id)
        figures.append({
            "figure_id": figure_id,
            "caption": str(link.get("caption") or link.get("summary") or ""),
        })
        doc_id = str(link.get("document_id") or "").strip()
        if doc_id:
            figure_document_ids[figure_id] = doc_id

    if not figures or not figure_document_ids:
        return figures

    by_document: dict[str, list[str]] = {}
    for fig_id, doc_id in figure_document_ids.items():
        by_document.setdefault(doc_id, []).append(fig_id)

    # figure_id -> (body, "approved"|"ai_candidate")
    explanations: dict[str, tuple[str, str]] = {}
    session = _pg_session()
    try:
        for doc_id, fig_ids in by_document.items():
            rows = element_explanations.list_for_document(
                session,
                doc_id,
                element_type=element_explanations.ELEMENT_TYPE_FIGURE,
                kind=element_explanations.KIND_CONTEXTUAL,
            )
            by_figure: dict[str, list[dict]] = {}
            for row in rows:
                fid = row.get("element_id")
                if fid in fig_ids:
                    by_figure.setdefault(fid, []).append(row)
            for fig_id in fig_ids:
                candidates = by_figure.get(fig_id) or []
                approved_row = next(
                    (r for r in candidates if r.get("status") == element_explanations.STATUS_APPROVED and r.get("body")),
                    None,
                )
                if approved_row:
                    explanations[fig_id] = (approved_row["body"], "approved")
                    continue
                candidate_row = next(
                    (r for r in candidates if r.get("status") == element_explanations.STATUS_CANDIDATE and r.get("body")),
                    None,
                )
                if candidate_row:
                    explanations[fig_id] = (candidate_row["body"], "ai_candidate")
    except Exception:
        logger.warning("Failed to load figure explanations for lecture studio prompt", exc_info=True)
        explanations = {}
    finally:
        session.close()

    for fig in figures:
        info = explanations.get(fig["figure_id"])
        if info:
            fig["explanation"], fig["explanation_status"] = info
    return figures


def _normalize_check_questions(value: object) -> list[dict]:
    """Normalize legacy string questions and detailed check-question objects."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    normalized: list[dict] = []
    for item in items:
        if isinstance(item, str):
            question = item.strip()
            if question:
                normalized.append({
                    "question": question,
                    "model_answer": "",
                    "answer_requirements": [],
                    "explanation": "",
                })
            continue
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or item.get("text") or "").strip()
        if not question:
            continue
        normalized.append({
            "question": question,
            "model_answer": str(item.get("model_answer") or item.get("answer") or "").strip(),
            "answer_requirements": _clean_str_list(
                item.get("answer_requirements")
                or item.get("required_elements")
                or item.get("requirements")
            ),
            "explanation": str(item.get("explanation") or item.get("rationale") or "").strip(),
        })
    return normalized


@router.put("/courses/{course_id}/lecture-studio/course-topics/{topic_id}")
def save_lecture_studio_course_topic(
    course_id: str,
    topic_id: str,
    body: dict,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """原稿スタジオで編集したコーストピックの授業用ドラフトを保存する。"""
    course_data = _course_data_for_studio(course_id, current_user)
    target = _find_course_topic(course_data, topic_id, body.get("chapter_index"), body.get("topic_index"))
    if target is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    student_material = body.get("student_material")
    if isinstance(student_material, dict):
        source_text = str(student_material.get("source_text") or "")
        target["student_material"] = {
            "source_format": student_material.get("source_format") or "eg-markdown-v1",
            "source_text": source_text,
        }
    target["key_concepts"] = _clean_str_list(body.get("key_concepts"))
    target["spoken_script"] = str(body.get("spoken_script") or "")
    target["cautions"] = _clean_str_list(body.get("cautions"))
    target["check_questions"] = _normalize_check_questions(body.get("check_questions"))

    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT user_id
                FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Course not found")
        if str(row[0]) != str(current_user["id"]) and current_user.get("role") != ROLE_SYSTEM_ADMIN:
            raise HTTPException(status_code=403, detail="Forbidden")
        session.execute(
            sa_text("""
                UPDATE learning_courses
                SET data = CAST(:data AS jsonb),
                    updated_at = now()
                WHERE id = :course_id
            """),
            {
                "course_id": course_id,
                "data": json.dumps(course_data, ensure_ascii=False),
            },
        )
        # トピックの授業用教材（student_material）/読み上げ原稿（spoken_script）が変わったため、
        # 生成済みのトピック音声を無効化する（原稿編集時のチャンク音声無効化と同じ方針。
        # 次回の音声生成で作り直される）。表示スライドと音声スライドの食い違いを防ぐ。
        session.execute(
            sa_text("""
                DELETE FROM topic_lecture_audio_cache
                WHERE course_id = :course_id AND topic_id = :topic_id
            """),
            {"course_id": course_id, "topic_id": topic_id},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to save course topic draft: course_id=%s topic_id=%s", course_id, topic_id)
        raise HTTPException(status_code=500, detail="Failed to save topic draft")
    finally:
        session.close()

    return {"course_id": course_id, "topic_id": topic_id, "status": "edited"}


_COURSE_TOPIC_DRAFT_PROMPT = """あなたは大学教員の授業用ドラフト作成を支援するアシスタントです。

目的:
- 教員が設定したコースの説明順序と想定受講者の理解を優先する
- Claim / コンポーネント / 数式 / 原文抜粋は、本文の主役ではなく根拠材料として使う
- 教材欄と本文説明を分離する

教材欄の表記:
- Markdown風の軽量表記を使う
- インライン数式は `$...$`
- ブロック数式は `$$...$$`
- `\(...\)` や `\[...\]` は使わず、必ず `$...$` / `$$...$$` を使う
- 埋め込みは `![[equation:id]]`, `![[figure:id]]`, `![[source:id]]`, `![[claim:id]]`, `![[component:id]]`
- `![[figure:id]]` は根拠候補の `figures` に列挙された `figure_id` のみ使用可（一覧に無い id を発明しない）

必ずJSONのみを返してください。
JSON文字列内のLaTeXバックスラッシュは必ず `\\Lambda` のように二重化してください。
形式:
{{
  "key_concepts": ["重要概念"],
  "student_material": {{"source_format": "eg-markdown-v1", "source_text": "学生に見せる教材"}},
  "spoken_script": "教員が話せる自然文。音声読み上げ対象。",
  "cautions": ["注意点"],
  "check_questions": [
    {{
      "question": "確認問題",
      "model_answer": "模範解答",
      "answer_requirements": ["回答に含めるべき要素"],
      "explanation": "難しい問題では、なぜそうなるかの解説"
    }}
  ]
}}

コーストピック:
{topic_json}

根拠候補:
{evidence_json}

現在の授業用ドラフト:
{draft_json}

教員の指示:
{instructor_prompt}
"""


class CourseTopicStudentMaterialDraft(BaseModel):
    source_format: str = "eg-markdown-v1"
    source_text: str = ""


class CourseTopicDraftLLMResponse(BaseModel):
    key_concepts: list[str] = Field(default_factory=list)
    student_material: CourseTopicStudentMaterialDraft = Field(default_factory=CourseTopicStudentMaterialDraft)
    spoken_script: str = ""
    cautions: list[str] = Field(default_factory=list)
    check_questions: list[dict] = Field(default_factory=list)


def _parse_course_topic_draft_json(raw: str) -> dict:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
      lines = cleaned.split("\n")
      lines = [ln for ln in lines if not ln.strip().startswith("```")]
      cleaned = "\n".join(lines).strip()

    candidates = [cleaned]
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match and match.group() != cleaned:
        candidates.append(match.group())

    for candidate in list(candidates):
        # LLMs often emit LaTeX like \Lambda inside JSON strings. JSON only
        # allows a small set of backslash escapes, so preserve those and
        # double every other single backslash before a second parse attempt.
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", candidate)
        if repaired != candidate:
            candidates.append(repaired)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate, strict=False)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            continue
    return {}


def _normalize_course_topic_draft_response(parsed: object) -> dict:
    if isinstance(parsed, BaseModel):
        parsed = parsed.model_dump()
    if not isinstance(parsed, dict):
        parsed = {}
    student_material = parsed.get("student_material")
    if isinstance(student_material, BaseModel):
        student_material = student_material.model_dump()
    if not isinstance(student_material, dict):
        student_material = {"source_format": "eg-markdown-v1", "source_text": str(student_material or "")}
    return {
        "key_concepts": _clean_str_list(parsed.get("key_concepts")),
        "student_material": {
            "source_format": student_material.get("source_format") or "eg-markdown-v1",
            "source_text": str(student_material.get("source_text") or ""),
        },
        "spoken_script": str(parsed.get("spoken_script") or ""),
        "cautions": _clean_str_list(parsed.get("cautions")),
        "check_questions": _normalize_check_questions(parsed.get("check_questions")),
    }


@router.post("/courses/{course_id}/lecture-studio/course-topics/{topic_id}/draft/rewrite")
def rewrite_lecture_studio_course_topic(
    course_id: str,
    topic_id: str,
    body: dict,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教員の指示に基づいてコーストピックの授業用ドラフトを生成する。"""
    course_data = _course_data_for_studio_editable(course_id, current_user)
    topic = _find_course_topic(course_data, topic_id, body.get("chapter_index"), body.get("topic_index"))
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    draft = {
        "key_concepts": body.get("key_concepts") if body.get("key_concepts") is not None else topic.get("key_concepts", []),
        "student_material": body.get("student_material") if body.get("student_material") is not None else topic.get("student_material", {}),
        "spoken_script": body.get("spoken_script") if body.get("spoken_script") is not None else topic.get("spoken_script", ""),
        "cautions": body.get("cautions") if body.get("cautions") is not None else topic.get("cautions", []),
        "check_questions": body.get("check_questions") if body.get("check_questions") is not None else topic.get("check_questions", []),
    }
    evidence = {
        "summary": topic.get("summary", ""),
        "source_excerpt": topic.get("source_excerpt", ""),
        "linked_component_ids": topic.get("linked_component_ids", []),
        "linked_equation_ids": topic.get("linked_equation_ids", []),
        "source_evidence_ids": topic.get("source_evidence_ids", []),
        "evidence_links": topic.get("evidence_links", []),
        "figures": _topic_figures_for_prompt(topic),
        "content_blocks": topic.get("content_blocks", []),
        "coverage": topic.get("coverage", {}),
        "content_confidence": topic.get("content_confidence", ""),
    }
    prompt = _COURSE_TOPIC_DRAFT_PROMPT.format(
        topic_json=json.dumps({
            "id": topic.get("id", ""),
            "title": topic.get("title", ""),
            "prerequisites": topic.get("prerequisites", []),
        }, ensure_ascii=False, indent=2)[:3000],
        evidence_json=json.dumps(evidence, ensure_ascii=False, indent=2)[:8000],
        draft_json=json.dumps(draft, ensure_ascii=False, indent=2)[:6000],
        instructor_prompt=str(body.get("prompt") or "")[:2000],
    )

    params = get_llm_params("fast")
    # M層 Phase 3（§6.3）: この実行だけのモデル上書き（scene "lecture_studio"）。
    # 未指定なら従来どおり fast tier。
    requested_model = str(body.get("model") or "").strip() or None
    if requested_model:
        reason = llm_policy.validate_model_for_scene(llm_policy.SCENE_LECTURE_STUDIO, requested_model)
        if reason:
            raise HTTPException(status_code=422, detail=reason)
    effective_model = requested_model or params["model"]
    effective_effort = None if requested_model else params["reasoning_effort"]

    parsed: object = {}
    consume_lecture_rewrite_quota(current_user["id"])
    with usage_context("admin:lecture_rewrite", user_id=current_user["id"], course_id=course_id):
        try:
            parsed = generate_text_with_structured_output(
                messages=[{"role": "user", "content": prompt}],
                response_format=CourseTopicDraftLLMResponse,
                model=effective_model,
            )
        except Exception as structured_exc:
            logger.warning(
                "Structured course topic draft failed; retrying text JSON parse course_id=%s topic_id=%s error=%s",
                course_id,
                topic_id,
                structured_exc,
            )
            try:
                raw = generate_text(
                    messages=[{"role": "user", "content": prompt}],
                    model=effective_model,
                    reasoning_effort=effective_effort,
                )
                parsed = _parse_course_topic_draft_json(raw)
            except Exception:
                logger.exception("AI course topic draft retry failed for course_id=%s topic_id=%s", course_id, topic_id)
                raise HTTPException(status_code=502, detail="AI draft generation failed")
    result = _normalize_course_topic_draft_response(parsed)
    if not any([
        result["key_concepts"],
        result["student_material"]["source_text"].strip(),
        result["spoken_script"].strip(),
        result["cautions"],
        result["check_questions"],
    ]):
        logger.error("AI course topic draft returned empty result for course_id=%s topic_id=%s", course_id, topic_id)
        raise HTTPException(status_code=502, detail="AI draft response was empty")

    # AI 書き換え耐性（教材図スタジオ設計書 §7.1b-3）: rewrite は本文を丸ごと再生成する
    # ため、トピックに紐づく図（evidence_links ∪ linked_figure_ids）の
    # ``![[figure:id]]`` が黙って消えることがある。消えていたら決定論的に末尾へ復元し、
    # 「位置がリセットされた」ことをフロントへ正直に伝える（黙って直さない）。
    result["figures_restored"] = _restore_missing_figure_embeds(result, topic)
    return result


def _restore_missing_figure_embeds(result: dict, topic: dict) -> bool:
    """rewrite 応答の本文から消えた図 embed を末尾へ復元する。復元したら True。

    注入そのものは ``core.course_content_builder._ensure_required_figures_in_material``
    （コース内容生成と同じ決定論注入）に委譲し、「何が欠けていたか」の判定だけを行う
    （空白の正規化だけで ``figures_restored`` が立たないようにするため）。
    """
    material = result.get("student_material")
    source_text = str(material.get("source_text") or "") if isinstance(material, dict) else ""
    missing = [
        item
        for item in _required_figure_items(topic)
        if item.get("figure_id") and f"![[figure:{item['figure_id']}]]" not in source_text
    ]
    if not missing:
        return False
    _ensure_required_figures_in_material(result, topic)
    return True


@router.get("/courses/{course_id}/lecture-studio/document-structure")
def get_lecture_studio_document_structure(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースに紐づく教材の文書構造を返す。

    優先順: document_analysis_runs._artifacts.document_structure > chunks のセクション情報
    """
    course_data = _course_data_for_studio(course_id, current_user)
    sources = course_sources(course_data)
    material_ids = course_source_material_ids(course_data)

    if not material_ids:
        return {"course_id": course_id, "documents": []}

    mid_placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
    params: dict = {f"mid_{i}": mid for i, mid in enumerate(material_ids)}

    # --- 教材 → document_id マッピング + タイトル取得 ---
    session = _pg_session()
    try:
        mat_rows = session.execute(
            sa_text(f"""
                SELECT m.id AS material_id, m.title AS material_title,
                       d.id AS document_id, d.title AS doc_title
                FROM materials m
                LEFT JOIN documents d ON d.material_id = m.id
                WHERE m.id IN ({mid_placeholders})
                ORDER BY m.id
            """),
            params,
        ).fetchall()
    finally:
        session.close()

    # material_id → {document_id, title}
    mat_map: dict[str, dict] = {}
    for r in mat_rows:
        mid = str(r[0])
        if mid not in mat_map:
            mat_map[mid] = {
                "material_id": mid,
                "material_title": r[1] or "",
                "document_id": str(r[2]) if r[2] else "",
                "doc_title": r[3] or "",
            }

    documents_out = []
    for source in sources:
        mid = source.get("material_id", "")
        if not mid or mid not in mat_map:
            continue
        mat = mat_map[mid]
        doc_id = mat["document_id"]

        # --- Agent復元済み文書構造を取得 ---
        agent_structure = None
        analysis_status = "not_started"
        if doc_id:
            session = _pg_session()
            try:
                run_row = session.execute(
                    sa_text("""
                        SELECT status, stage_outputs
                        FROM document_analysis_runs
                        WHERE document_id = :doc_id
                        ORDER BY created_at DESC
                        LIMIT 1
                    """),
                    {"doc_id": doc_id},
                ).fetchone()
            finally:
                session.close()
            if run_row:
                analysis_status = run_row[0] or "not_started"
                stage_outputs = run_row[1] or {}
                if isinstance(stage_outputs, str):
                    try:
                        stage_outputs = json.loads(stage_outputs)
                    except Exception:
                        stage_outputs = {}
                artifacts = stage_outputs.get("_artifacts") or {}
                agent_structure = artifacts.get("document_structure")

        # --- チャンクベースのフォールバック構造 ---
        chunks = _get_course_chunks(course_data)
        doc_chunks = [c for c in chunks if (c.get("document_id") or c.get("material_id")) == (doc_id or mid)]
        sections: dict[str, dict] = {}
        for c in doc_chunks:
            sid = c.get("section_id") or "default"
            if sid not in sections:
                sections[sid] = {
                    "section_id": sid,
                    "title": c.get("section_title") or sid,
                    "level": c.get("section_level", 0),
                    "order": c.get("section_order", 0),
                    "chunks": [],
                }
            sections[sid]["chunks"].append({
                "chunk_id": c["id"],
                "chunk_index": c.get("chunk_index", 0),
                "text": (c.get("text") or "")[:200],
                "page_start": c.get("page_start"),
                "page_end": c.get("page_end"),
                "status": _chunk_status(c),
            })
        sections_list = sorted(sections.values(), key=lambda s: s["order"])

        documents_out.append({
            "material_id": mid,
            "material_title": source.get("title") or mat["material_title"],
            "document_id": doc_id,
            "analysis_status": analysis_status,
            "agent_structure": agent_structure,
            "sections": sections_list,
        })

    return {"course_id": course_id, "documents": documents_out}


@router.get("/courses/{course_id}/lecture-studio/components")
def get_lecture_studio_components(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースの理論コンポーネント一覧とグラフを返す。

    - ``theory_components`` から全コンポーネントを取得
    - ``theory_component_graphs`` からすべての依存グラフを取得
    - ``theory_claims`` から各コンポーネントに紐づくclaimを集約
    """
    course_data = _course_data_for_studio(course_id, current_user)
    source_document_ids: list[str] = []
    material_ids = course_source_material_ids(course_data)
    if material_ids:
        mid_placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
        params_mid: dict = {f"mid_{i}": mid for i, mid in enumerate(material_ids)}
        session = _pg_session()
        try:
            doc_rows = session.execute(
                sa_text(f"SELECT DISTINCT document_id FROM chunks WHERE material_id IN ({mid_placeholders})"),
                params_mid,
            ).fetchall()
            source_document_ids = [str(r[0]) for r in doc_rows if r[0]]
        finally:
            session.close()

    component_filter = "course_id = :course_id"
    component_params: dict = {"course_id": course_id}
    if source_document_ids:
        doc_placeholders = ", ".join(f":doc_{i}" for i in range(len(source_document_ids)))
        component_filter = f"({component_filter} OR document_id IN ({doc_placeholders}))"
        component_params.update({f"doc_{i}": doc_id for i, doc_id in enumerate(source_document_ids)})

    # --- theory_components ---
    session = _pg_session()
    try:
        comp_rows = session.execute(
            sa_text(f"""
                SELECT id, course_id, primary_chunk_id, name, component_type, summary, status,
                       source_chunks, inputs, outputs, preconditions, constraints,
                       invalid_conditions, dependencies, blackbox_policy, validation_warnings,
                       teacher_notes, source_scope, evidence_claims, maturity_level, maturity_source,
                       review_status, cautions, connectors, created_at, updated_at,
                       component_type_text, internal_flow, duplicate_candidates
                FROM theory_components
                WHERE {component_filter}
                ORDER BY updated_at DESC, created_at DESC
            """),
            component_params,
        ).fetchall()
    finally:
        session.close()

    def _jv(v: object, default: object) -> object:
        if v is None:
            return default
        if isinstance(v, (dict, list)):
            return v
        if isinstance(v, str) and v.strip():
            try:
                return json.loads(v)
            except Exception:
                pass
        return default

    components = []
    for row in comp_rows:
        components.append({
            "id": str(row[0]),
            "course_id": row[1],
            "primary_chunk_id": str(row[2]) if row[2] else None,
            "name": row[3] or "",
            "component_type": row[26] or row[4] or "",
            "summary": row[5] or "",
            "status": row[6] or "candidate",
            "source_chunks": _jv(row[7], []),
            "inputs": _jv(row[8], []),
            "outputs": _jv(row[9], []),
            "preconditions": _jv(row[10], []),
            "dependencies": _jv(row[13], []),
            "evidence_claims": _jv(row[18], []),
            "maturity_level": row[19] or "paper_claim",
            "review_status": row[21] or "teacher_review_required",
            "cautions": _jv(row[22], []),
        })

    # --- theory_component_graphs ---
    graph_edges: list[dict] = []
    graph_nodes: list[dict] = []
    session = _pg_session()
    try:
        graph_rows = session.execute(
            sa_text(f"""
                SELECT document_id, graph_json, validation_results
                FROM theory_component_graphs
                WHERE {component_filter}
                ORDER BY updated_at DESC
            """),
            component_params,
        ).fetchall()
    finally:
        session.close()
    graphs_by_doc: list[dict] = []
    for row in graph_rows:
        gj = _jv(row[1], {})
        graphs_by_doc.append({
            "document_id": row[0],
            "edges": _jv(gj.get("edges") if isinstance(gj, dict) else None, []),  # type: ignore[attr-defined]
            "nodes": _jv(gj.get("nodes") if isinstance(gj, dict) else None, []),  # type: ignore[attr-defined]
            "validation_results": _jv(row[2], []),
        })
        graph_edges.extend(_jv(gj.get("edges") if isinstance(gj, dict) else None, []))  # type: ignore[attr-defined]
        graph_nodes.extend(_jv(gj.get("nodes") if isinstance(gj, dict) else None, []))  # type: ignore[attr-defined]

    # --- analysis status ---
    analysis_status = "not_started"
    if source_document_ids:
        session = _pg_session()
        try:
            status_rows = session.execute(
                sa_text("""
                    SELECT status FROM document_analysis_runs
                    WHERE document_id = ANY(:doc_ids)
                    ORDER BY created_at DESC
                    LIMIT 10
                """),
                {"doc_ids": source_document_ids},
            ).fetchall()
        finally:
            session.close()
        statuses = [r[0] for r in status_rows]
        if "running" in statuses:
            analysis_status = "running"
        elif "completed" in statuses:
            analysis_status = "completed"
        elif "failed" in statuses:
            analysis_status = "failed"
        elif "pending" in statuses:
            analysis_status = "pending"

    return {
        "course_id": course_id,
        "analysis_status": analysis_status,
        "components": components,
        "graphs_by_document": graphs_by_doc,
        "all_edges": graph_edges,
        "all_nodes": graph_nodes,
    }
