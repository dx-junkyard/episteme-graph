"""既存テーブルからの決定論的な状態投影（S1: 保存しない・毎回導出）。

documents / document_analysis_runs / learning_courses / chunks / lecture_audio_cache /
course_group_permissions を読むだけで、新規の正本テーブルは持たない。FastAPI / LLM
クライアントを import しない（S3）。投影できないエンティティは unknown を返す（S5）。
"""
from __future__ import annotations

import datetime
import logging

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from core.lecture import compute_material_audio_readiness, get_course_lecture_language

from . import schema

logger = logging.getLogger(__name__)


def _iso(value) -> str:
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return ""


# ---------------------------------------------------------------------------
# MaterialStatus
# ---------------------------------------------------------------------------


def _fetch_document(session: Session, document_ref: str) -> dict | None:
    row = session.execute(
        sa_text("""
            SELECT id::text, source_path, status, chunk_count, updated_at
            FROM documents
            WHERE id::text = :ref OR source_path = :ref
            LIMIT 1
        """),
        {"ref": document_ref},
    ).mappings().fetchone()
    return dict(row) if row else None


def _fetch_latest_run(session: Session, document_id: str, material_id: str) -> dict | None:
    row = session.execute(
        sa_text("""
            SELECT id::text, status, current_stage, error_message, updated_at, completed_at
            FROM document_analysis_runs
            WHERE document_id = :document_id OR material_id = :material_id
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"document_id": document_id, "material_id": material_id},
    ).mappings().fetchone()
    return dict(row) if row else None


def project_material_status(session: Session, document_ref: str) -> schema.MaterialStatus:
    """教材（ドキュメント）の現在状態を投影する。投影不能なら unknown（S5）。"""
    doc = _fetch_document(session, document_ref)
    if not doc:
        return schema.MaterialStatus(
            document_id="", material_id=document_ref, state=schema.MATERIAL_STATE_UNKNOWN,
        )

    document_id = doc["id"]
    material_id = doc["source_path"] or document_id
    run = _fetch_latest_run(session, document_id, material_id)

    if run is None:
        doc_status = doc["status"]
        state_map = {
            "uploaded": schema.MATERIAL_STATE_UPLOADED,
            "processing": schema.MATERIAL_STATE_CHUNKING,
            "completed": schema.MATERIAL_STATE_ANALYZED,
            "failed": schema.MATERIAL_STATE_ANALYSIS_FAILED,
        }
        state = state_map.get(doc_status, schema.MATERIAL_STATE_UNKNOWN)
        return schema.MaterialStatus(
            document_id=document_id, material_id=material_id, state=state,
            updated_at=_iso(doc["updated_at"]),
        )

    run_status = run["status"]
    if run_status == "pending":
        state = schema.MATERIAL_STATE_CHUNKING
        stage, reason = "", ""
    elif run_status == "running":
        state = schema.MATERIAL_STATE_ANALYZING
        stage, reason = run["current_stage"] or "", ""
    elif run_status == "failed":
        state = schema.MATERIAL_STATE_ANALYSIS_FAILED
        stage, reason = run["current_stage"] or "", run["error_message"] or ""
    elif run_status == "completed":
        # 成果物（S1: chunk_count>0 = 抽出成功の代理指標。theory_components は
        # course_id 紐づけでドキュメント単独共有（migration 035）に対応しないため使わない）
        if (doc["chunk_count"] or 0) > 0:
            state = schema.MATERIAL_STATE_ANALYZED
        else:
            state = schema.MATERIAL_STATE_ANALYZING
        stage, reason = run["current_stage"] or "", ""
    else:
        state = schema.MATERIAL_STATE_UNKNOWN
        stage, reason = "", ""

    occurred_at = run["completed_at"] or run["updated_at"]
    return schema.MaterialStatus(
        document_id=document_id, material_id=material_id, state=state,
        stage=stage, reason=reason, run_id=run["id"], updated_at=_iso(occurred_at),
    )


def list_material_statuses(session: Session, document_refs: list[str]) -> list[schema.MaterialStatus]:
    return [project_material_status(session, ref) for ref in document_refs]


# ---------------------------------------------------------------------------
# CourseStatus
# ---------------------------------------------------------------------------


def _fetch_course(session: Session, course_id: str) -> dict | None:
    row = session.execute(
        sa_text("""
            SELECT id, data, is_published, updated_at
            FROM learning_courses
            WHERE id = :course_id
            LIMIT 1
        """),
        {"course_id": course_id},
    ).mappings().fetchone()
    return dict(row) if row else None


def _course_material_ids(data: dict) -> list[str]:
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, list):
        return []
    return [s.get("material_id") for s in sources if isinstance(s, dict) and s.get("material_id")]


def _course_atlas_bound(data: dict) -> bool:
    if not isinstance(data, dict) or not data.get("cartridge_id"):
        return False
    for chapter in data.get("chapters") or []:
        if not isinstance(chapter, dict):
            continue
        for topic in chapter.get("topics") or []:
            if isinstance(topic, dict) and topic.get("atlas_node_id"):
                return True
    for topic in data.get("topics") or []:
        if isinstance(topic, dict) and topic.get("atlas_node_id"):
            return True
    return False


def _course_shared(session: Session, course_id: str) -> bool:
    row = session.execute(
        sa_text("SELECT 1 FROM course_group_permissions WHERE course_id = :cid LIMIT 1"),
        {"cid": course_id},
    ).fetchone()
    return row is not None


def project_course_status(session: Session, course_id: str) -> schema.CourseStatus:
    """コースの現在状態をチェックポイント集合として投影する（順序を強制しない）。"""
    course = _fetch_course(session, course_id)
    if not course:
        return schema.CourseStatus(course_id=course_id, registered=False, script_status=schema.SCRIPT_STATUS_DRAFT)

    data = course["data"] if isinstance(course["data"], dict) else {}
    material_ids = _course_material_ids(data)

    total_chunks = 0
    generated_chunks = 0
    if material_ids:
        chunk_rows = session.execute(
            sa_text("SELECT id, spoken_text FROM chunks WHERE material_id = ANY(:ids)"),
            {"ids": material_ids},
        ).fetchall()
        total_chunks = len(chunk_rows)
        generated_chunks = sum(1 for r in chunk_rows if r[1])

    if total_chunks == 0 or generated_chunks == 0:
        script_status = schema.SCRIPT_STATUS_DRAFT
    elif generated_chunks == total_chunks:
        script_status = schema.SCRIPT_STATUS_GENERATED
    else:
        script_status = schema.SCRIPT_STATUS_PARTIAL

    # 音声 readiness はスライド単位 + 言語一致（core/lecture.py の正本判定, Tier2-11）で
    # 決める。旧実装は chunk 単位の粗い判定（lecture_audio_cache に1行でもあれば
    # readiness とみなす）で、api/routes/lecture.py::get_topic_audio_status の
    # スライド単位判定と食い違い得た。
    lecture_language = get_course_lecture_language(data)
    audio_readiness = compute_material_audio_readiness(session, material_ids, lecture_language)
    total_slides = audio_readiness["total_slides"]
    ready_slides = audio_readiness["ready_slides"]

    if total_slides == 0 or ready_slides == 0:
        audio_status = schema.AUDIO_STATUS_NONE
    elif ready_slides == total_slides:
        audio_status = schema.AUDIO_STATUS_GENERATED
    else:
        audio_status = schema.AUDIO_STATUS_PARTIAL

    return schema.CourseStatus(
        course_id=course_id,
        registered=True,
        script_status=script_status,
        audio_status=audio_status,
        atlas_bound=_course_atlas_bound(data),
        published=bool(course["is_published"]),
        shared=_course_shared(session, course_id),
        updated_at=_iso(course["updated_at"]),
    )


def list_course_statuses(session: Session, course_ids: list[str]) -> list[schema.CourseStatus]:
    return [project_course_status(session, cid) for cid in course_ids]
