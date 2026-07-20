"""既存テーブルからの決定論的な状態投影（S1: 保存しない・毎回導出）。

documents / document_analysis_runs / learning_courses / chunks / lecture_audio_cache /
topic_lecture_audio_cache（migration 047、N18: トピック教材経路の音声 readiness）/
object_group_permissions（migration 044、統合前は専用テーブル=migration 010）を読むだけで、
新規の正本テーブルは持たない。FastAPI / LLM クライアントを import しない（S3）。
投影できないエンティティは unknown を返す（S5）。
"""
from __future__ import annotations

import datetime
import logging

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from core.course_data import course_atlas_binding_facts, course_source_material_ids
from core.lecture import compute_course_audio_readiness, get_course_lecture_language

from . import schema

# 後方互換の再エクスポート（Tier3-18: 正本は core/course_data.py に移設済み）。
# 呼び出し側（core/admin_assistant/next_steps.py 等）は本モジュール経由の import を
# そのまま使い続けられる。
__all__ = ["course_atlas_binding_facts"]

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


# 行データ (documents の status/chunk_count/updated_at + 最新 run) から MaterialStatus を
# 導出する純関数（Tier3-16）。project_material_status（単体）と
# project_material_statuses_bulk（バッチ）の両方がこれを使い、状態導出ロジックを1本化する。
def derive_material_status(
    document_id: str,
    material_id: str,
    doc_status: str | None,
    chunk_count: int | None,
    doc_updated_at,
    run: dict | None,
) -> schema.MaterialStatus:
    if run is None:
        state_map = {
            "uploaded": schema.MATERIAL_STATE_UPLOADED,
            "processing": schema.MATERIAL_STATE_CHUNKING,
            "completed": schema.MATERIAL_STATE_ANALYZED,
            "failed": schema.MATERIAL_STATE_ANALYSIS_FAILED,
        }
        state = state_map.get(doc_status, schema.MATERIAL_STATE_UNKNOWN)
        return schema.MaterialStatus(
            document_id=document_id, material_id=material_id, state=state,
            updated_at=_iso(doc_updated_at),
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
        if (chunk_count or 0) > 0:
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
    return derive_material_status(
        document_id, material_id, doc["status"], doc["chunk_count"], doc["updated_at"], run,
    )


# ---------------------------------------------------------------------------
# バッチ導出（N+1回避, Tier3-16）— documents 1クエリ + document_analysis_runs 1クエリ。
# ---------------------------------------------------------------------------


def _fetch_documents_bulk(session: Session, document_refs: list[str]) -> dict[str, dict | None]:
    """document_ref（documents.id または source_path）-> documents 行、を1クエリで返す。"""
    refs = [r for r in document_refs if r]
    if not refs:
        return {}
    rows = session.execute(
        sa_text("""
            SELECT id::text, source_path, status, chunk_count, updated_at
            FROM documents
            WHERE id::text = ANY(:refs) OR source_path = ANY(:refs)
        """),
        {"refs": refs},
    ).mappings().fetchall()
    by_id: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    for row in rows:
        d = dict(row)
        by_id[d["id"]] = d
        if d["source_path"]:
            by_source[d["source_path"]] = d
    return {ref: (by_id.get(ref) or by_source.get(ref)) for ref in refs}


def _fetch_latest_runs_bulk(
    session: Session, document_ids: list[str], material_ids: list[str],
) -> tuple[dict[str, dict], dict[str, dict]]:
    """(document_id -> 最新run, material_id -> 最新run) を1クエリで返す。

    `_fetch_latest_run` と同じ「document_id OR material_id」一致方針を保ったまま
    複数教材分をまとめて1クエリにする。created_at DESC で並べ、各キーについて
    最初に出現した行（＝最新）だけを採用する。
    """
    ids = sorted({i for i in (list(document_ids) + list(material_ids)) if i})
    if not ids:
        return {}, {}
    rows = session.execute(
        sa_text("""
            SELECT id::text, document_id, material_id, status, current_stage,
                   error_message, stage_outputs, updated_at, completed_at, created_at
            FROM document_analysis_runs
            WHERE document_id = ANY(:ids) OR material_id = ANY(:ids)
            ORDER BY created_at DESC
        """),
        {"ids": ids},
    ).mappings().all()
    by_document_id: dict[str, dict] = {}
    by_material_id: dict[str, dict] = {}
    for row in rows:
        d = dict(row)
        doc_id = d.get("document_id")
        mat_id = d.get("material_id")
        if doc_id and doc_id not in by_document_id:
            by_document_id[doc_id] = d
        if mat_id and mat_id not in by_material_id:
            by_material_id[mat_id] = d
    return by_document_id, by_material_id


def project_material_statuses_bulk(
    session: Session, document_refs: list[str],
) -> dict[str, schema.MaterialStatus]:
    """複数教材の MaterialStatus を documents 1クエリ + document_analysis_runs 1クエリで導出する。

    document_ref（呼び出し側が渡した元の参照文字列）をキーに返す。投影不能な ref は
    unknown（S5, project_material_status と同じ fail-closed 挙動）。
    """
    refs = list(dict.fromkeys(r for r in document_refs if r))
    if not refs:
        return {}

    docs_by_ref = _fetch_documents_bulk(session, refs)
    doc_ids = [d["id"] for d in docs_by_ref.values() if d]
    material_ids = [(d["source_path"] or d["id"]) for d in docs_by_ref.values() if d]
    latest_by_doc, latest_by_mat = _fetch_latest_runs_bulk(session, doc_ids, material_ids)

    result: dict[str, schema.MaterialStatus] = {}
    for ref in refs:
        doc = docs_by_ref.get(ref)
        if not doc:
            result[ref] = schema.MaterialStatus(
                document_id="", material_id=ref, state=schema.MATERIAL_STATE_UNKNOWN,
            )
            continue
        document_id = doc["id"]
        material_id = doc["source_path"] or document_id
        run = latest_by_doc.get(document_id) or latest_by_mat.get(material_id)
        result[ref] = derive_material_status(
            document_id, material_id, doc["status"], doc["chunk_count"], doc["updated_at"], run,
        )
    return result


def list_material_statuses(session: Session, document_refs: list[str]) -> list[schema.MaterialStatus]:
    by_ref = project_material_statuses_bulk(session, document_refs)
    return [
        by_ref.get(ref) or schema.MaterialStatus(
            document_id="", material_id=ref, state=schema.MATERIAL_STATE_UNKNOWN,
        )
        for ref in document_refs
    ]


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
    return course_source_material_ids(data)


def _course_atlas_bound(data: dict) -> bool:
    has_cartridge, has_atlas_node = course_atlas_binding_facts(data)
    return has_cartridge and has_atlas_node


def _course_shared(session: Session, course_id: str) -> bool:
    # object_type='course' を必ず付けること（無いと document 共有まで誤カウントする regression）。
    row = session.execute(
        sa_text(
            "SELECT 1 FROM object_group_permissions "
            "WHERE object_type = 'course' AND object_id = :cid LIMIT 1"
        ),
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
    # スライド単位判定と食い違い得た。N18: チャンク経路に加えトピック教材経路
    # （lecture_uses_topic_material が真のトピック。topic_lecture_audio_cache +
    # 読み上げ原稿の充足）も合算する（読み上げ原稿が無いトピックスライドは ready に
    # ならないため、draft 未充足のコースが generated に化けない）。
    lecture_language = get_course_lecture_language(data)
    audio_readiness = compute_course_audio_readiness(session, course_id, data, lecture_language)
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
