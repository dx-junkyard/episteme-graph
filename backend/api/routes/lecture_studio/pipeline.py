"""Episteme Graph — Lecture Script Studio: Agent Pipeline・再解析系 (Tier 3-17a パッケージ分割)。

- コース教材の構造再解析 (structure/reanalyze)
- 新 document-first Agent Pipeline のコース単位 / 教材単位実行
- コース内容生成・コース単位アクティブタスク照会
"""

from __future__ import annotations

import json
import logging
import threading
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text as sa_text

from dependencies import ROLE_SYSTEM_ADMIN, _require_teacher
from schemas import BackgroundTaskOut
from services import (
    create_background_task,
    get_active_task_for_course,
    get_editable_course_data,
    get_viewable_course_data,
    reanalyze_course_structure_background,
    update_background_task,
)
from core.course_data import course_source_material_ids
from core.postgres import get_session as _pg_session
from core.storage import get_storage_client

from ._shared import _get_system_admin_course_data

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Lecture Script Studio"])


DOCUMENT_PIPELINE_STAGE_LABELS: dict[str, str] = {
    "document_structure": "DocumentStructureAgent",
    "paper_skeleton": "PaperSkeletonAgent",
    "rhetorical_role": "RhetoricalRoleAgent",
    "claim_qualification": "ClaimQualificationAgent",
    "equation_semantics": "EquationSemanticsAgent",
    "evidence_registry": "EvidenceRegistryBuilder",
    "claim_object_builder": "ClaimObjectBuilder",
    "symbol_registry": "SymbolRegistryBuilder",
    "derivation_chain": "DerivationChainAgent",
    "figure_table_semantics": "FigureTableSemanticsAgent",
    "thesis_reconstruction": "ThesisReconstructionAgent",
    "dsl_linking": "DSLLinkingAgent",
    "component_assembly": "ComponentAssemblyAgent",
    "component_graph": "ComponentGraphAgent",
    "narrative_annotator": "NarrativeAnnotator",
    "course_mapping": "CourseMappingAgent",
    "blueprint": "BlueprintAgent",
    "export_validation": "ExportValidationGate",
}


# ---------------------------------------------------------------------------
# 5. コース教材の構造再解析
# ---------------------------------------------------------------------------


@router.post("/courses/{course_id}/structure/reanalyze")
def reanalyze_course_structure(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """既存チャンクを維持したまま、構造DSL/変数/ancestorsを再解析する。"""
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    material_ids = list(dict.fromkeys(
        mid.strip() for mid in course_source_material_ids(course_data)
    ))
    if not material_ids:
        raise HTTPException(status_code=400, detail="No source materials linked to this course")

    active = get_active_task_for_course(course_id)
    if active:
        raise HTTPException(status_code=409, detail="Another course task is already running")

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "structure_reanalysis", current_user["id"])
    update_background_task(task_id, "pending", result_data={
        "course_id": course_id,
        "total_materials": len(material_ids),
        "processed_materials": 0,
        "updated_chunks": 0,
        "errors": 0,
        "progress": 0,
        "stage": "queued",
    })

    thread = threading.Thread(
        target=reanalyze_course_structure_background,
        args=(course_id, course_data, task_id),
        daemon=True,
    )
    thread.start()

    logger.info(
        "structure reanalysis accepted: task=%s course=%s materials=%d by user=%s",
        task_id, course_id, len(material_ids), current_user["id"],
    )
    return {
        "task_id": task_id,
        "course_id": course_id,
        "total_materials": len(material_ids),
        "status": "pending",
    }


# ---------------------------------------------------------------------------
# 5b. 新 Agent Pipeline のコース単位実行
# ---------------------------------------------------------------------------


def _course_pipeline_documents(course_data: dict) -> list[dict]:
    material_ids = list(dict.fromkeys(
        mid.strip() for mid in course_source_material_ids(course_data)
    ))
    material_ids = [mid for mid in material_ids if mid]
    if not material_ids:
        return []

    params = {f"mid_{i}": mid for i, mid in enumerate(material_ids)}
    placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text(
                f"""
                SELECT id::text, COALESCE(source_path, ''), COALESCE(filename, title, 'document.pdf')
                FROM documents
                WHERE source_path IN ({placeholders})
                ORDER BY created_at ASC
                """
            ),
            params,
        ).fetchall()
    finally:
        session.close()
    return [
        {"document_id": row[0], "material_id": row[1], "filename": row[2] or "document.pdf"}
        for row in rows
        if row[0] and row[1]
    ]


def _pipeline_source_kind_from_name(name: str | None) -> str | None:
    lower = (name or "").lower()
    if lower.endswith(".tar.gz") or lower.endswith(".tgz"):
        return "tex_archive"
    if lower.endswith(".pdf"):
        return "pdf"
    return None


def _pipeline_source_candidates(material_id: str, filename: str) -> list[tuple[str, str]]:
    """Return storage object candidates paired with the source_kind they imply.

    Older rows may have a title-like filename without the original extension, so
    probing only by filename can incorrectly fall back to PDF. Always include the
    canonical upload object names and infer source_kind from the object found.
    """
    result: list[tuple[str, str]] = []

    def add(object_name: str | None, source_kind: str | None = None) -> None:
        if not object_name:
            return
        kind = source_kind or _pipeline_source_kind_from_name(object_name) or "pdf"
        pair = (object_name, kind)
        if pair not in result:
            result.append(pair)

    filename_kind = _pipeline_source_kind_from_name(filename)
    if filename_kind == "tex_archive":
        add(f"uploads/{material_id}.tar.gz", "tex_archive")
        add(f"uploads/{material_id}.tgz", "tex_archive")
        add(filename, "tex_archive")
        add(f"uploads/{material_id}.pdf", "pdf")
    elif filename_kind == "pdf":
        add(f"uploads/{material_id}.pdf", "pdf")
        add(filename, "pdf")
        add(f"uploads/{material_id}.tar.gz", "tex_archive")
        add(f"uploads/{material_id}.tgz", "tex_archive")
    else:
        add(f"uploads/{material_id}.tar.gz", "tex_archive")
        add(f"uploads/{material_id}.tgz", "tex_archive")
        add(f"uploads/{material_id}.pdf", "pdf")
        add(filename, None)
    add(material_id, filename_kind or "pdf")
    return result


def _load_pipeline_source(material_id: str, filename: str) -> tuple[bytes, str]:
    """教材ファイルをストレージからロードし、(bytes, source_kind) を返す。

    filename 拡張子と canonical upload object 名から source_kind を判定する。
    """
    storage = get_storage_client()
    attempted: list[str] = []
    for object_name, source_kind in _pipeline_source_candidates(material_id, filename):
        attempted.append(f"{object_name}:{source_kind}")
        try:
            return storage.get_object("raw-papers", object_name), source_kind
        except Exception:
            continue
    raise FileNotFoundError(
        f"Source object not found for material {material_id} "
        f"(tried={', '.join(attempted)})"
    )


def _set_document_pipeline_status(document_id: str, status: str) -> None:
    session = _pg_session()
    try:
        session.execute(
            sa_text(
                "UPDATE documents SET status = :status, updated_at = now() "
                "WHERE id = CAST(:document_id AS uuid)"
            ),
            {"document_id": document_id, "status": status},
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to update document status: document=%s status=%s", document_id, status, exc_info=True)
    finally:
        session.close()


def _get_editable_material_document(material_id: str, current_user: dict) -> dict:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT id::text, source_path, COALESCE(filename, title, 'document.pdf'), uploaded_by::text
                FROM documents
                WHERE source_path = :material_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"material_id": material_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise HTTPException(status_code=404, detail="Material not found")
    if current_user.get("role") != ROLE_SYSTEM_ADMIN and str(row[3]) != str(current_user.get("id")):
        raise HTTPException(status_code=403, detail="Material is not editable")
    return {
        "document_id": row[0],
        "material_id": row[1] or material_id,
        "filename": row[2] or "document.pdf",
    }


def _get_active_task_for_material(material_id: str) -> dict | None:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT id, task_type, status, result_data, error_message, created_at, updated_at
                FROM background_tasks
                WHERE status IN ('pending', 'processing')
                  AND result_data IS NOT NULL
                  AND result_data->>'material_id' = :material_id
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"material_id": material_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return None
    return {
        "task_id": row[0],
        "task_type": row[1],
        "status": row[2],
        "result_data": row[3] or {},
        "error_message": row[4],
        "created_at": row[5].isoformat() if row[5] else "",
        "updated_at": row[6].isoformat() if row[6] else "",
    }


def _material_pipeline_status(material_id: str, document_id: str) -> dict:
    stages = {stage: "not_started" for stage in DOCUMENT_PIPELINE_STAGE_LABELS}
    status = "not_started"
    current_stage = ""
    error_message = ""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT status, current_stage, error_message, stage_outputs
                FROM document_analysis_runs
                WHERE (document_id = :document_id OR material_id = :material_id)
                  AND (run_type IS NULL OR run_type <> 'revision')
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"document_id": document_id, "material_id": material_id},
        ).fetchone()
    finally:
        session.close()

    degraded_stages: list = []
    available_features: list = ["rag_chat"]  # chunks は常に使える
    if row:
        status = row[0] or "not_started"
        current_stage = row[1] or ""
        error_message = row[2] or ""
        stage_outputs = row[3] or {}
        if isinstance(stage_outputs, str):
            try:
                stage_outputs = json.loads(stage_outputs)
            except Exception:
                stage_outputs = {}
        if isinstance(stage_outputs, dict):
            for stage in stages:
                info = stage_outputs.get(stage)
                if isinstance(info, dict):
                    stages[stage] = info.get("status") or ("completed" if info.get("progress") == 100 else "not_started")
            # 縮退ステージ情報を persist artifact から取得
            persist_info = stage_outputs.get("persist_claims_components_graph") or {}
            if isinstance(persist_info, dict):
                degraded_stages = list(persist_info.get("degraded_stages") or [])
                if not persist_info.get("components_skipped"):
                    available_features.append("components")
                if not persist_info.get("graph_skipped"):
                    available_features.append("component_graph")
            elif status == "completed":
                # 縮退情報なし = 全機能利用可能
                available_features.extend(["components", "component_graph"])

    active = _get_active_task_for_material(material_id)
    if active:
        result_data = active.get("result_data") or {}
        status = "running"
        current_stage = result_data.get("stage") or current_stage
        target_stage = result_data.get("target_stage") or ""
        start_stage = result_data.get("start_stage") or ""
        if target_stage:
            stages[target_stage] = "running"
        elif start_stage:
            stages[start_stage] = "running"
        elif current_stage in stages:
            stages[current_stage] = "running"

    if status == "failed" and current_stage in stages:
        stages[current_stage] = "failed"

    is_degraded = bool(degraded_stages)
    # 縮退状態を retry ヒントとして提示（最初の縮退ステージから再実行を促す）
    retry_suggestion = degraded_stages[0] if degraded_stages else ""

    return {
        "material_id": material_id,
        "document_id": document_id,
        "status": status,
        "current_stage": current_stage,
        "error_message": error_message,
        "stages": stages,
        "active_task_id": active["task_id"] if active else "",
        "active_target_stage": ((active.get("result_data") or {}).get("target_stage") if active else "") or "",
        "active_start_stage": ((active.get("result_data") or {}).get("start_stage") if active else "") or "",
        # 縮退状態情報 (フロントエンドが「何が使えて何が使えないか」を表示するために使う)
        "is_degraded": is_degraded,
        "degraded_stages": degraded_stages,
        "available_features": available_features,
        "retry_suggestion": retry_suggestion,
    }


def _material_document_pipeline_worker(
    *,
    task_id: str,
    user_id: str,
    document: dict,
    target_stage: str | None,
    start_stage: str | None = None,
) -> None:
    from core.document_pipeline import PipelineStageError, run_document_pipeline

    material_id = document["material_id"]
    document_id = document["document_id"]
    label_stage = start_stage or target_stage or ""
    label = DOCUMENT_PIPELINE_STAGE_LABELS.get(label_stage, "Agent Pipeline")
    current_stage = start_stage or target_stage or "document_pipeline"

    def publish(status: str = "processing", progress: int = 0, error_message: str | None = None) -> None:
        update_background_task(
            task_id,
            status,
            result_data={
                "document_id": document_id,
                "material_id": material_id,
                "stage": current_stage,
                "target_stage": target_stage or "",
                "start_stage": start_stage or "",
                "label": label,
                "generated": 1 if status == "completed" else 0,
                "failed": 1 if status == "failed" else 0,
                "skipped": 0,
                "total_documents": 1,
                "total_chunks": 1,
                "progress": progress,
            },
            error_message=error_message,
        )

    publish("processing", 0)
    try:
        source_bytes, source_kind = _load_pipeline_source(material_id, document["filename"])
        if target_stage is None:
            _set_document_pipeline_status(document_id, "processing")

        def on_stage(stage: str, info: dict) -> None:
            nonlocal current_stage
            current_stage = stage
            publish("processing", int((info or {}).get("progress") or 0))

        run_document_pipeline(
            pdf_bytes=source_bytes,
            document_id=document_id,
            material_id=material_id,
            filename=document["filename"],
            source_kind=source_kind,
            course_id=None,
            progress_callback=on_stage,
            target_stage=target_stage,
            start_stage=start_stage,
            resume=target_stage is not None or start_stage is not None,
        )
        if target_stage is None:
            _set_document_pipeline_status(document_id, "completed")
        current_stage = target_stage or "completed"
        publish("completed", 100)
    except Exception as exc:
        stage = getattr(exc, "stage", current_stage) if isinstance(exc, PipelineStageError) else current_stage
        current_stage = stage or "failed"
        logger.exception("Material document pipeline failed: task=%s material=%s stage=%s", task_id, material_id, current_stage)
        if target_stage is None:
            _set_document_pipeline_status(document_id, "failed")
        publish("failed", 100, str(exc))


def _course_document_pipeline_worker(
    *,
    task_id: str,
    course_id: str,
    user_id: str,
    documents: list[dict],
    target_stage: str | None,
    start_stage: str | None = None,
) -> None:
    from core.course_content_builder import build_course_content
    from core.document_pipeline import PipelineStageError, run_document_pipeline

    total = len(documents)
    label_stage = start_stage or target_stage or ""
    label = DOCUMENT_PIPELINE_STAGE_LABELS.get(label_stage, "Agent Pipeline")
    generated = 0
    failed = 0
    current_document = ""
    current_stage = start_stage or target_stage or "started"

    def publish(status: str = "processing", error_message: str | None = None) -> None:
        progress = int((generated + failed) / total * 100) if total else 100
        update_background_task(
            task_id,
            status,
            result_data={
                "course_id": course_id,
                "stage": current_stage,
                "target_stage": target_stage or "",
                "start_stage": start_stage or "",
                "label": label,
                "current_document_id": current_document,
                "generated": generated,
                "failed": failed,
                "skipped": 0,
                "total_documents": total,
                "total_chunks": total,
                "progress": progress,
            },
            error_message=error_message,
        )

    publish("processing")
    try:
        for index, doc in enumerate(documents, start=1):
            current_document = doc["document_id"]
            current_stage = start_stage or target_stage or "document_pipeline"
            publish("processing")
            source_bytes, source_kind = _load_pipeline_source(doc["material_id"], doc["filename"])
            if target_stage is None:
                _set_document_pipeline_status(doc["document_id"], "processing")

            def on_stage(stage: str, info: dict) -> None:
                nonlocal current_stage
                current_stage = stage
                stage_progress = int(info.get("progress") or 0) if isinstance(info, dict) else 0
                overall = int(((index - 1) + (stage_progress / 100)) / total * 100) if total else 100
                update_background_task(
                    task_id,
                    "processing",
                    result_data={
                        "course_id": course_id,
                        "stage": stage,
                        "target_stage": target_stage or "",
                        "start_stage": start_stage or "",
                        "label": label,
                        "current_document_id": current_document,
                        "generated": generated,
                        "failed": failed,
                        "skipped": 0,
                        "total_documents": total,
                        "total_chunks": total,
                        "progress": overall,
                    },
                )

            run_document_pipeline(
                pdf_bytes=source_bytes,
                document_id=doc["document_id"],
                material_id=doc["material_id"],
                filename=doc["filename"],
                source_kind=source_kind,
                course_id=course_id,
                progress_callback=on_stage,
                target_stage=target_stage,
                start_stage=start_stage,
                resume=target_stage is not None or start_stage is not None,
            )
            if target_stage is None:
                _set_document_pipeline_status(doc["document_id"], "completed")
            generated += 1
            publish("processing")
    except Exception as exc:
        failed += 1
        stage = getattr(exc, "stage", current_stage) if isinstance(exc, PipelineStageError) else current_stage
        logger.exception("Course document pipeline failed: task=%s course=%s stage=%s", task_id, course_id, stage)
        current_stage = stage or "failed"
        if target_stage is None and current_document:
            _set_document_pipeline_status(current_document, "failed")
        publish("failed", str(exc))
        return

    if target_stage in (None, "equation_semantics", "component_assembly", "course_mapping"):
        current_stage = "course_content"
        publish("processing")
        try:
            build_course_content(user_id, course_id)
        except Exception:
            logger.warning("Course content build after pipeline failed: course=%s task=%s", course_id, task_id, exc_info=True)

    current_stage = target_stage or "completed"
    publish("completed")


@router.post("/courses/{course_id}/document-pipeline/run")
def run_course_document_pipeline(
    course_id: str,
    body: dict | None = Body(default=None),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コース配下の document-first Agent Pipeline を起動する。

    ``target_stage`` 指定時は、その stage だけを単独再実行してそこで終了する。
    """
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    target_stage = str((body or {}).get("target_stage") or "").strip() or None
    start_stage = str((body or {}).get("start_stage") or "").strip() or None
    if target_stage and target_stage not in DOCUMENT_PIPELINE_STAGE_LABELS:
        raise HTTPException(status_code=400, detail="Unknown pipeline stage")
    if start_stage and start_stage not in DOCUMENT_PIPELINE_STAGE_LABELS:
        raise HTTPException(status_code=400, detail="Unknown pipeline start stage")

    documents = _course_pipeline_documents(course_data)
    if not documents:
        raise HTTPException(status_code=400, detail="No source documents linked to this course")

    active = get_active_task_for_course(course_id)
    if active:
        raise HTTPException(status_code=409, detail="Another course task is already running")

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "document_pipeline", current_user["id"])
    update_background_task(task_id, "pending", result_data={
        "course_id": course_id,
        "stage": start_stage or target_stage or "queued",
        "target_stage": target_stage or "",
        "start_stage": start_stage or "",
        "label": DOCUMENT_PIPELINE_STAGE_LABELS.get(start_stage or target_stage or "", "Agent Pipeline"),
        "generated": 0,
        "failed": 0,
        "skipped": 0,
        "total_documents": len(documents),
        "total_chunks": len(documents),
        "progress": 0,
    })

    thread = threading.Thread(
        target=_course_document_pipeline_worker,
        kwargs={
            "task_id": task_id,
            "course_id": course_id,
            "user_id": current_user["id"],
            "documents": documents,
            "target_stage": target_stage,
            "start_stage": start_stage,
        },
        daemon=True,
    )
    thread.start()

    logger.info(
        "course document pipeline accepted: task=%s course=%s docs=%d stage=%s by user=%s",
        task_id, course_id, len(documents), start_stage or target_stage or "full", current_user["id"],
    )
    return {
        "task_id": task_id,
        "course_id": course_id,
        "total_documents": len(documents),
        "target_stage": target_stage or "",
        "start_stage": start_stage or "",
        "status": "pending",
    }


@router.get("/materials/{material_id}/document-pipeline/status")
def get_material_document_pipeline_status(
    material_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教材単位の document-first Agent Pipeline 状態を返す。"""
    document = _get_editable_material_document(material_id, current_user)
    return _material_pipeline_status(document["material_id"], document["document_id"])


@router.post("/materials/{material_id}/document-pipeline/run")
def run_material_document_pipeline(
    material_id: str,
    body: dict | None = Body(default=None),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教材単位の document-first Agent Pipeline を起動する。"""
    target_stage = str((body or {}).get("target_stage") or "").strip() or None
    start_stage = str((body or {}).get("start_stage") or "").strip() or None
    if target_stage and target_stage not in DOCUMENT_PIPELINE_STAGE_LABELS:
        raise HTTPException(status_code=400, detail="Unknown pipeline stage")
    if start_stage and start_stage not in DOCUMENT_PIPELINE_STAGE_LABELS:
        raise HTTPException(status_code=400, detail="Unknown pipeline start stage")

    document = _get_editable_material_document(material_id, current_user)
    active = _get_active_task_for_material(document["material_id"])
    if active:
        raise HTTPException(status_code=409, detail="Another material task is already running")

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "material_document_pipeline", current_user["id"])
    update_background_task(task_id, "pending", result_data={
        "document_id": document["document_id"],
        "material_id": document["material_id"],
        "stage": start_stage or target_stage or "queued",
        "target_stage": target_stage or "",
        "start_stage": start_stage or "",
        "label": DOCUMENT_PIPELINE_STAGE_LABELS.get(start_stage or target_stage or "", "Agent Pipeline"),
        "generated": 0,
        "failed": 0,
        "skipped": 0,
        "total_documents": 1,
        "total_chunks": 1,
        "progress": 0,
    })

    thread = threading.Thread(
        target=_material_document_pipeline_worker,
        kwargs={
            "task_id": task_id,
            "user_id": current_user["id"],
            "document": document,
            "target_stage": target_stage,
            "start_stage": start_stage,
        },
        daemon=True,
    )
    thread.start()

    return {
        "task_id": task_id,
        "material_id": document["material_id"],
        "document_id": document["document_id"],
        "target_stage": target_stage or "",
        "start_stage": start_stage or "",
        "status": "pending",
    }


def _course_owner_id(course_id: str) -> str:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT user_id::text FROM learning_courses WHERE id = :course_id LIMIT 1"),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    return str(row[0]) if row and row[0] else ""


def _course_content_generation_worker(task_id: str, course_id: str, owner_id: str) -> None:
    from core.course_content_builder import build_course_content

    update_background_task(task_id, "processing", result_data={
        "course_id": course_id,
        "stage": "course_content",
        "progress": 0,
    })
    try:
        result = build_course_content(owner_id, course_id)
        status = result.get("status") if isinstance(result, dict) else ""
        progress_status = "completed" if status == "completed" else "failed"
        update_background_task(task_id, progress_status, result_data={
            "course_id": course_id,
            "stage": "course_content",
            "progress": 100,
            "result": result,
        }, error_message="" if progress_status == "completed" else (result or {}).get("message", "コース内容生成に失敗しました"))
    except Exception as exc:
        logger.exception("Course content generation failed: task=%s course=%s", task_id, course_id)
        update_background_task(task_id, "failed", result_data={
            "course_id": course_id,
            "stage": "course_content",
            "progress": 100,
        }, error_message=str(exc))


@router.post("/courses/{course_id}/course-content/generate")
def generate_course_content(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """CourseMapping/ComponentAssembly 成果物からコース内容を再生成する。"""
    course_data = get_editable_course_data(current_user["id"], course_id)
    if not course_data and current_user.get("role") == ROLE_SYSTEM_ADMIN:
        course_data = _get_system_admin_course_data(course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    active = get_active_task_for_course(course_id)
    if active:
        raise HTTPException(status_code=409, detail="Another course task is already running")

    owner_id = _course_owner_id(course_id)
    if not owner_id:
        raise HTTPException(status_code=404, detail="Course not found")

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "course_content_generation", current_user["id"])
    update_background_task(task_id, "pending", result_data={
        "course_id": course_id,
        "stage": "course_content",
        "progress": 0,
    })
    thread = threading.Thread(
        target=_course_content_generation_worker,
        args=(task_id, course_id, owner_id),
        daemon=True,
    )
    thread.start()
    return {"task_id": task_id, "course_id": course_id, "status": "pending"}


# ---------------------------------------------------------------------------
# 6. コース単位のアクティブタスク照会 (Issue #139)
# ---------------------------------------------------------------------------


@router.get("/courses/{course_id}/tasks/active")
def get_course_active_task(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict | None:
    """コースに紐づく進行中タスク (pending/processing) のうち最新1件を返す。

    重複実行防止およびリロード後のポーリング再開に使用する。
    進行中タスクが無い場合は null を返す。
    """
    course_data = get_viewable_course_data(current_user["id"], course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")

    task = get_active_task_for_course(course_id)
    if not task:
        return None
    return BackgroundTaskOut(**task).model_dump()
