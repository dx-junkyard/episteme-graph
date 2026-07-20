"""Episteme Graph — 管理者エンドポイント (/api/admin)。"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text as sa_text

from dependencies import (
    _get_current_user,
    _hash_password,
    _require_system_admin,
    _require_teacher,
    ROLE_STUDENT,
    ROLE_TEACHER,
)
from schemas import (
    ApproveWithScopeRequest,
    BackgroundTaskOut,
    CourseBuilderChatRequest,
    CourseBuilderChatResponse,
    CourseBuilderSessionCreate,
    CourseBuilderSessionOut,
    CourseBuilderSessionUpdate,
    CourseGroupPermissionOut,
    CourseGroupPermissionUpsertRequest,
    CreateUserRequest,
    DeleteConfirmRequest,
    DocumentGroupPermissionOut,
    DocumentGroupPermissionUpsertRequest,
    MaterialOut,
    ReextractionJobOut,
    SimulationResponse,
    SchemaProposalOut,
    SchemaTypeCreateRequest,
    SchemaTypeOut,
    UserOut,
    VisibilityUpdateRequest,
)
from services import (
    _material_lock,
    _material_status,
    _resolve_document,
    create_background_task,
    extract_pdf_pages,
    get_background_task,
    get_course_group_permissions,
    get_document_group_permissions,
    get_user_group_ids,
    process_material_background,
    record_review_event,
    save_cb_session,
    user_can_access_group,
    user_can_edit_course,
    user_can_view_course,
    user_can_view_document,
    user_owns_course,
    user_owns_document,
)
from core.config import get_settings
from core.course_data import (
    course_cartridge_id,
    course_chapters,
    course_sources,
    course_title as _course_title,
    course_topics,
)
from core.document_pipeline.figure_images import load_document_figures
from core.document_pipeline.persistence import get_latest_analysis_run
from core.llm import generate_text
from core.llm_usage.context import usage_context
from core.llm_worker.client import resolve_model
from core.llm_worker.cost_gate import CostGate, today_str
from core.llm_worker.history import window_history
from core.meta_analyzer import (
    analyze_unanswered_queries,
    approve_proposal,
    get_proposals,
    reject_proposal,
)
from core.postgres import get_session as _pg_session
from core.reextractor import enqueue_reextraction, get_jobs as get_reextraction_jobs
from core.schema import AUDIT_ENTITY_DOCUMENT_SHARE
from core.schema_registry import (
    add_ontology_type,
    add_predicate,
    get_ontology_types,
    get_predicates,
)
from core.status import projector as status_projector
from core.status import schema as status_schema
from core.storage import get_storage_client
# 画像パイプライン §7: 図画像 API は theory_components.py の
# _ensure_document_viewable（document_id は UUID / material_id 両対応）を必ず通す。
from routes.theory_components import _ensure_document_viewable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin"])

_TEX_ARCHIVE_SUFFIXES = (".tar.gz", ".tgz")


def _uploaded_source_kind(filename: str | None) -> str | None:
    lower = (filename or "").lower()
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith(_TEX_ARCHIVE_SUFFIXES):
        return "tex_archive"
    return None


def _source_object_suffix(filename: str | None, source_kind: str) -> str:
    lower = (filename or "").lower()
    if source_kind == "tex_archive":
        return ".tgz" if lower.endswith(".tgz") else ".tar.gz"
    return ".pdf"


def _source_content_type(source_kind: str) -> str:
    if source_kind == "tex_archive":
        return "application/gzip"
    return "application/pdf"


def _source_title(filename: str | None) -> str:
    name = filename or "document"
    lower = name.lower()
    for suffix in (".tar.gz", ".tgz", ".pdf"):
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return os.path.splitext(name)[0]


def _word_set(text: str) -> set[str]:
    """テキストを正規化して3文字以上の単語セットを返す。"""
    return {w for w in re.findall(r"[A-Za-z0-9぀-鿿]+", text.lower()) if len(w) >= 3}


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _normalized_chars(text: str, *, max_chars: int = 20000) -> str:
    """ページ推定用に空白差分を除いた比較文字列へ正規化する。"""
    return re.sub(r"\s+", "", (text or "").replace("\x00", "").lower())[:max_chars]


def _char_ngrams(text: str, *, size: int = 5) -> set[str]:
    normalized = _normalized_chars(text)
    if len(normalized) < size:
        return {normalized} if normalized else set()
    return {normalized[i : i + size] for i in range(len(normalized) - size + 1)}


def _infer_chunk_page_range(
    chunk_text: str,
    pages: list[dict],
    *,
    max_window_pages: int = 3,
    min_score: float = 0.04,
    candidates: list[tuple[int, int, set[str]]] | None = None,
) -> tuple[int | None, int | None, float]:
    """PDFページ本文と既存チャンク本文を照合し、最も近いページ範囲を推定する。"""
    chunk_grams = _char_ngrams(chunk_text)
    if not chunk_grams:
        return None, None, 0.0

    best_start: int | None = None
    best_end: int | None = None
    best_score = 0.0
    if candidates is None:
        candidates = _build_page_window_candidates(pages, max_window_pages=max_window_pages)
    for page_start, page_end, page_grams in candidates:
        score = _jaccard(chunk_grams, page_grams)
        if score > best_score:
            best_score = score
            best_start = page_start
            best_end = page_end

    if best_score < min_score:
        return None, None, best_score
    return best_start, best_end, best_score


def _build_page_window_candidates(
    pages: list[dict],
    *,
    max_window_pages: int = 3,
) -> list[tuple[int, int, set[str]]]:
    """連続ページの候補 n-gram を事前計算する。"""
    candidates: list[tuple[int, int, set[str]]] = []
    page_count = len(pages)
    for start_idx in range(page_count):
        combined_text = ""
        for end_idx in range(start_idx, min(page_count, start_idx + max_window_pages)):
            combined_text = f"{combined_text}\n{pages[end_idx].get('text') or ''}"
            page_grams = _char_ngrams(combined_text)
            if page_grams:
                candidates.append(
                    (
                        int(pages[start_idx]["page"]),
                        int(pages[end_idx]["page"]),
                        page_grams,
                    )
                )
    return candidates


def _backfill_missing_chunk_pages_from_pdf(material_id: str, pdf_bytes: bytes) -> int:
    """ページ情報が欠けている既存チャンクへ、再登録PDFから推定したページ情報を補完する。"""
    pages = extract_pdf_pages(pdf_bytes)
    pages = [page for page in pages if (page.get("text") or "").strip()]
    if not pages:
        return 0
    candidates = _build_page_window_candidates(pages)
    if not candidates:
        return 0

    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, text
                FROM chunks
                WHERE material_id = :mid
                  AND (page_start IS NULL OR page_end IS NULL)
                ORDER BY chunk_index, id
            """),
            {"mid": material_id},
        ).fetchall()

        updated = 0
        for row in rows:
            page_start, page_end, score = _infer_chunk_page_range(
                row[1] or "",
                pages,
                candidates=candidates,
            )
            if page_start is None or page_end is None:
                logger.debug(
                    "Skipped chunk page backfill: material=%s chunk=%s score=%.3f",
                    material_id,
                    row[0],
                    score,
                )
                continue
            session.execute(
                sa_text("""
                    UPDATE chunks
                    SET page_start = :page_start, page_end = :page_end
                    WHERE id = :chunk_id
                """),
                {
                    "page_start": page_start,
                    "page_end": page_end,
                    "chunk_id": row[0],
                },
            )
            updated += 1

        if updated:
            session.commit()
        return updated
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


@router.post("/materials/upload", status_code=202)
def upload_material(
    file: UploadFile = File(...),
    analyze_images: bool = Form(False),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """PDF/TeX教材をアップロードし、バックグラウンドでグラフ化処理を開始する。

    即座に task_id を返却し、処理完了はポーリングで確認する。

    ``analyze_images``（画像パイプライン §3）: True のとき ``apparatus_semantics``
    ステージ（vision LLM による装置図同定）が有効になる。既定 off（原則6:
    コスト fail-closed）。``figure_image_extraction``（非LLM の図画像抽出）は
    このフラグに関係なく常時実行される（決定 0-4-2）。
    """
    import datetime

    source_kind = _uploaded_source_kind(file.filename)
    if source_kind is None:
        raise HTTPException(status_code=400, detail="Only PDF files or TeX .tar.gz archives are accepted")

    source_bytes = file.file.read()
    if len(source_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    material_id = str(uuid.uuid4())[:12]
    object_suffix = _source_object_suffix(file.filename, source_kind)
    source_object_name = f"uploads/{material_id}{object_suffix}"
    doc_id = uuid.uuid4()
    task_id = str(uuid.uuid4())[:12]
    now = datetime.datetime.utcnow().isoformat()

    try:
        get_storage_client().upload_bytes(
            "raw-papers",
            source_object_name,
            source_bytes,
            content_type=_source_content_type(source_kind),
        )
    except Exception:
        logger.exception("Failed to store uploaded source for material %s", material_id)
        raise HTTPException(status_code=500, detail="Source storage failed")

    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO documents (id, title, filename, status, uploaded_by, doc_type, source_path)
                VALUES (:id, :title, :filename, 'uploaded', CAST(:uploaded_by AS uuid), 'textbook', :material_id)
            """),
            {
                "id": doc_id,
                "title": _source_title(file.filename),
                "filename": file.filename,
                "uploaded_by": current_user["id"],
                "material_id": material_id,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    create_background_task(task_id, "material_processing", current_user["id"])

    thread = threading.Thread(
        target=process_material_background,
        args=(material_id, str(doc_id), file.filename, source_bytes, task_id, None, source_kind),
        kwargs={"options": {"analyze_images": bool(analyze_images)}},
        daemon=True,
    )
    thread.start()

    logger.info(
        "Material upload accepted: %s (%s) task=%s by user=%s analyze_images=%s",
        material_id, file.filename, task_id, current_user["id"], analyze_images,
    )

    return {
        "task_id": task_id,
        "material_id": material_id,
        "filename": file.filename,
        "title": _source_title(file.filename),
        "source_kind": source_kind,
        "status": "pending",
        "uploaded_at": now,
        "analyze_images": bool(analyze_images),
    }


class ReanalyzeRequest(BaseModel):
    """画像パイプライン §3: 再解析時の analyze_images オプション（任意 body）。

    ``None``（未指定）は「前回 run の options を引き継ぐ」。orchestrator の継承分岐
    （options is None → 最新 run の options を再利用）を生かすため、
    ``reanalyze_document`` は ``options=None`` を渡す。明示 true/false のときのみ
    上書きする。かつては未指定でも常に ``{"analyze_images": False}`` を渡していた
    ため継承分岐が到達不能で、画像解析 ON で作った未レビューの AI 図分類
    （suggested_mode / analysis_profile）が再解析のたびに無警告で消えていた。"""
    analyze_images: bool | None = None


@router.post("/documents/{document_id}/reanalyze", status_code=202)
def reanalyze_document(
    document_id: str,
    body: ReanalyzeRequest | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """新Agent Pipeline (issue #226) で document を再解析する。

    保存済み PDF を MinIO から取り出し、process_material_background と同じ
    pipeline をバックグラウンド起動する。旧 course/section/chunk 単位の解析
    エンドポイントの後継。

    ``body.analyze_images``（画像パイプライン §3）: True のとき
    ``apparatus_semantics`` ステージを有効にする。省略（None）時は前回 run の
    options を引き継ぐ（初回解析で ON にした選択が再解析で黙って落ちない）。
    前回 run が無い場合の実効値は orchestrator 側の既定で False
    （明示オプトインのみ有効、原則6）。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text(
                "SELECT id, title, filename, source_path FROM documents "
                "WHERE id = CAST(:id AS uuid) LIMIT 1"
            ),
            {"id": document_id},
        ).fetchone()
    finally:
        session.close()

    if not row:
        raise HTTPException(status_code=404, detail="Document not found")

    material_id = row[3]
    filename = row[2] or row[1] or "document.pdf"

    if not material_id:
        raise HTTPException(
            status_code=400,
            detail="Document has no material_id; cannot fetch PDF for reanalysis",
        )

    filename_kind = _uploaded_source_kind(filename)
    object_candidates = []
    if filename_kind:
        object_candidates.append(f"uploads/{material_id}{_source_object_suffix(filename, filename_kind)}")
    object_candidates.extend([
        f"uploads/{material_id}.pdf",
        f"uploads/{material_id}.tar.gz",
        f"uploads/{material_id}.tgz",
        filename,
        material_id,
    ])
    storage = get_storage_client()
    source_bytes: bytes | None = None
    source_kind: str | None = filename_kind
    for object_name in object_candidates:
        if not object_name:
            continue
        try:
            source_bytes = storage.get_object("raw-papers", object_name)
            if source_kind is None:
                source_kind = _uploaded_source_kind(object_name) or "pdf"
            break
        except Exception:
            continue
    if source_bytes is None:
        raise HTTPException(status_code=404, detail="Source object not found in MinIO")

    task_id = str(uuid.uuid4())[:12]
    create_background_task(task_id, "document_reanalysis", current_user["id"])

    session = _pg_session()
    try:
        session.execute(
            sa_text(
                "UPDATE documents SET status = 'processing', updated_at = now() "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"id": document_id},
        )
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()

    # None（body 無し / analyze_images 未指定）は options=None を渡し、orchestrator の
    # 「前回 run の options を引き継ぐ」分岐を生かす。明示 true/false のみ上書きする。
    analyze_images = body.analyze_images if body is not None else None
    options = {"analyze_images": bool(analyze_images)} if analyze_images is not None else None
    thread = threading.Thread(
        target=process_material_background,
        args=(material_id, document_id, filename, source_bytes, task_id, None, source_kind or "pdf"),
        kwargs={"options": options},
        daemon=True,
    )
    thread.start()

    logger.info(
        "Document reanalysis accepted: doc=%s material=%s task=%s by user=%s",
        document_id, material_id, task_id, current_user["id"],
    )
    return {
        "task_id": task_id,
        "document_id": document_id,
        "material_id": material_id,
        "status": "pending",
    }


# Tier3-16: list_materials / get_material の status 合成を core/status/projector.py に委譲する。
# projector の6値語彙 (uploaded/chunking/analyzing/analyzed/analysis_failed/unknown) のうち、
# 実際に run が存在し running/failed/completed のいずれかであるときにのみ生まれる3状態だけを
# legacy 4値語彙 (uploaded/processing/completed/failed) へマップする。
# uploaded/chunking/unknown は意図的にマップしない（None を返す）: これらは
# 「run が無い」または「run が pending」のときにしか生まれず、その場合は現行どおり
# 上書きしない（documents.status の生値 or アップロード直後のメモリ上書き値を維持する）。
_LEGACY_MATERIAL_STATUS_OVERRIDE = {
    status_schema.MATERIAL_STATE_ANALYZING: "processing",
    status_schema.MATERIAL_STATE_ANALYZED: "completed",
    status_schema.MATERIAL_STATE_ANALYSIS_FAILED: "failed",
}


def _legacy_material_status(mstatus: status_schema.MaterialStatus) -> str | None:
    """projector の MaterialStatus を admin API のレガシー4値語彙へ変換する。

    None を返す場合、呼び出し側は既存の status（documents.status の生値 or
    アップロード直後のメモリ上書き値）をそのまま使う。
    """
    return _LEGACY_MATERIAL_STATUS_OVERRIDE.get(mstatus.state)


@router.get("/materials", response_model=list[MaterialOut])
def list_materials(
    include: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> list[MaterialOut]:
    """アップロード済み教材の一覧を返す。

    Issue #128: 「自分がアップロードしたもの」に加え、以下を含める。
    - visibility='public' の教材
    - visibility='group' かつ自分の参加グループで共有された教材
    - 自分が editor/viewer 権限を持つコースで参照されている教材
      （object_group_permissions object_type='course' 経由）

    ``include=summary`` を指定すると、教材選択UI向けにサマリ情報
    （主要 theory component 名・件数・文書構造の見出し・コース作成済みか）を
    既存テーブルから追加集約して返す。指定しない場合の挙動は従来どおり。
    """
    want_summary = bool(include and "summary" in include)
    user_groups = get_user_group_ids(current_user["id"])

    params: dict = {"user_id": current_user["id"]}
    group_clause = "FALSE"
    course_mat_clause = "FALSE"
    doc_perm_clause = "FALSE"

    if user_groups:
        gph = ", ".join(f"CAST(:g_{i} AS uuid)" for i in range(len(user_groups)))
        for i, gid in enumerate(user_groups):
            params[f"g_{i}"] = gid
        group_clause = f"(d.visibility = 'group' AND d.group_id IN ({gph}))"
        course_mat_clause = f"""
            d.source_path IN (
                SELECT (jsonb_array_elements(lc.data->'sources')->>'material_id')
                FROM learning_courses lc
                JOIN object_group_permissions cgp
                    ON cgp.object_type = 'course' AND cgp.object_id = lc.id
                JOIN group_members gm ON gm.group_id = cgp.group_id
                WHERE gm.user_id = CAST(:user_id AS uuid)
                  AND cgp.group_id IN ({gph})
                  AND cgp.permission IN ('viewer', 'editor')
                  AND lc.data IS NOT NULL
                  AND jsonb_typeof(lc.data->'sources') = 'array'
            )
        """
        # migration 044: 解析成果をグループへ直接共有（object_group_permissions object_type='document' 経由）
        doc_perm_clause = f"""
            d.id::text IN (
                SELECT dgp.object_id
                FROM object_group_permissions dgp
                JOIN group_members gm ON gm.group_id = dgp.group_id
                WHERE dgp.object_type = 'document'
                  AND gm.user_id = CAST(:user_id AS uuid)
                  AND dgp.group_id IN ({gph})
                  AND dgp.permission IN ('viewer', 'editor')
            )
        """

    session = _pg_session()
    try:
        records = session.execute(
            sa_text(f"""
                SELECT d.source_path, d.filename, d.title, d.status, d.created_at, d.knowledge_graph,
                       COALESCE(d.visibility, 'private'), d.group_id,
                       COALESCE(d.chunk_count, cs.chunk_count, 0) AS chunk_count,
                       d.id::text AS document_id,
                       COALESCE(d.authors, '{{}}') AS authors, d.year, d.doc_type
                FROM documents d
                LEFT JOIN (
                    SELECT material_id, COUNT(*) AS chunk_count
                    FROM chunks
                    GROUP BY material_id
                ) cs ON cs.material_id = d.source_path
                WHERE d.filename IS NOT NULL
                  AND (
                      d.uploaded_by = CAST(:user_id AS uuid)
                      OR d.visibility = 'public'
                      OR {group_clause}
                      OR {course_mat_clause}
                      OR {doc_perm_clause}
                  )
                ORDER BY d.created_at DESC
            """),
            params,
        ).fetchall()

        material_ids = [r[0] for r in records if r[0]]
        latest_runs: dict[str, dict] = {}
        if material_ids:
            run_params = {f"mid_{i}": mid for i, mid in enumerate(material_ids)}
            run_placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
            run_rows = session.execute(
                sa_text(
                    f"""
                    SELECT DISTINCT ON (material_id)
                           id::text AS id, material_id, status, current_stage, error_message,
                           stage_outputs, options, updated_at, completed_at
                    FROM document_analysis_runs
                    WHERE material_id IN ({run_placeholders})
                    ORDER BY material_id, created_at DESC
                    """
                ),
                run_params,
            ).mappings().all()
            latest_runs = {row["material_id"]: dict(row) for row in run_rows}

        # --- サマリ集約（?include=summary のときのみ）---------------------
        # 教材選択UI向け。すべて既存テーブルからの読み取りで、A層は非改変。
        components_by_mid: dict[str, list[str]] = {}
        materials_with_course: set[str] = set()
        if want_summary and records:
            uuid_to_mid = {r[9]: r[0] for r in records if r[9]}
            doc_uuids = [u for u in uuid_to_mid if u]
            if doc_uuids:
                uuid_ph = ", ".join(f":u_{i}" for i in range(len(doc_uuids)))
                uuid_params = {f"u_{i}": u for i, u in enumerate(doc_uuids)}
                comp_rows = session.execute(
                    sa_text(
                        f"""
                        SELECT document_id::text, name
                        FROM theory_components
                        WHERE document_id IN ({uuid_ph}) AND name IS NOT NULL
                        ORDER BY document_id,
                            CASE review_status WHEN 'teacher_reviewed' THEN 0 ELSE 1 END,
                            CASE maturity_level
                                WHEN 'peer_reviewed' THEN 0 WHEN 'textbook' THEN 1
                                WHEN 'paper_claim' THEN 2 ELSE 3 END
                        """
                    ),
                    uuid_params,
                ).fetchall()
                for uid, name in comp_rows:
                    mid = uuid_to_mid.get(uid)
                    if mid:
                        components_by_mid.setdefault(mid, []).append(name)

            # 自分がこの教材からコースを作成済みか（コース未作成リスト用）
            course_mid_rows = session.execute(
                sa_text(
                    """
                    SELECT DISTINCT (jsonb_array_elements(lc.data->'sources')->>'material_id') AS mid
                    FROM learning_courses lc
                    WHERE lc.user_id = CAST(:user_id AS uuid)
                      AND lc.data IS NOT NULL
                      AND jsonb_typeof(lc.data->'sources') = 'array'
                    """
                ),
                {"user_id": current_user["id"]},
            ).fetchall()
            materials_with_course = {row[0] for row in course_mid_rows if row[0]}
    finally:
        session.close()

    # MinIO に元ソースが存在する教材のIDセットを一括取得
    existing_pdf_ids: set[str] = set()
    try:
        for obj_name in get_storage_client().list_objects("raw-papers", "uploads/"):
            if obj_name.endswith(".pdf"):
                existing_pdf_ids.add(obj_name[len("uploads/"):-len(".pdf")])
            elif obj_name.endswith(".tar.gz"):
                existing_pdf_ids.add(obj_name[len("uploads/"):-len(".tar.gz")])
            elif obj_name.endswith(".tgz"):
                existing_pdf_ids.add(obj_name[len("uploads/"):-len(".tgz")])
    except Exception:
        pass  # MinIO 不達の場合は全件 has_pdf=False のまま

    materials = []
    for r in records:
        mid = r[0] or ""
        kg = r[5] if r[5] else None

        status = r[3] or "uploaded"
        with _material_lock:
            if mid in _material_status:
                status = _material_status[mid].get("status", status)

        run = latest_runs.get(mid)
        run_data = run or {}
        stage_outputs = run_data.get("stage_outputs") or {}
        current_stage = run_data.get("current_stage")
        stage_info = stage_outputs.get(current_stage) if current_stage else None
        if not isinstance(stage_info, dict):
            stage_info = {}

        # Tier3-16: run の有無・状態からの status 合成は projector に一本化する
        # （get_material と同一ロジック。一覧と詳細の status を一致させる）。
        mstatus = status_projector.derive_material_status(
            document_id=r[9], material_id=mid, doc_status=r[3],
            chunk_count=r[8], doc_updated_at=None, run=run,
        )
        legacy_status = _legacy_material_status(mstatus)
        if legacy_status is not None:
            status = legacy_status

        completed_at = run_data.get("completed_at")
        analyzed_at = (
            completed_at.isoformat()
            if completed_at is not None and status == "completed"
            else None
        )

        # メタデータ（authors / year / doc_type は常時付与）
        authors = list(r[10]) if r[10] else []
        year = r[11]
        doc_type = r[12]

        # サマリ（?include=summary のときのみ）
        component_names: list[str] = []
        top_concepts: list[str] = []
        section_outline: list[str] = []
        has_course = False
        if want_summary:
            component_names = components_by_mid.get(mid, [])
            has_course = mid in materials_with_course
            # legacy knowledge_graph の概念名
            if isinstance(kg, dict):
                for c in (kg.get("concepts") or [])[:8]:
                    name = c.get("name") if isinstance(c, dict) else str(c)
                    if name:
                        top_concepts.append(name)
            # 文書構造の見出し
            doc_structure = (stage_outputs.get("_artifacts") or {}).get("document_structure")
            if isinstance(doc_structure, dict):
                struct_sections = doc_structure.get("sections") or doc_structure.get("blocks") or []
                for sec in struct_sections[:12]:
                    if not isinstance(sec, dict):
                        continue
                    sec_title = sec.get("title") or sec.get("heading") or sec.get("label")
                    if sec_title:
                        section_outline.append(str(sec_title))

        uploaded_at = r[4].isoformat() if r[4] else ""
        materials.append(MaterialOut(
            material_id=mid,
            document_id=r[9],
            filename=r[1] or "",
            title=r[2] or "",
            status=status,
            uploaded_at=uploaded_at,
            chunk_count=int(r[8] or 0),
            knowledge_graph=kg,
            visibility=r[6] or "private",
            group_id=str(r[7]) if r[7] else None,
            has_pdf=mid in existing_pdf_ids,
            analysis_stage=current_stage,
            analysis_progress=stage_info.get("progress"),
            analysis_processed=stage_info.get("processed"),
            analysis_total=stage_info.get("total"),
            analysis_error=run_data.get("error_message") or None,
            # 最新 run の options（JSONB）。run が無ければ None（フロント契約: analysis_options）。
            analysis_options=(run_data.get("options") if run else None),
            authors=authors,
            year=year,
            doc_type=doc_type,
            analyzed_at=analyzed_at,
            component_count=(len(component_names) if want_summary else None),
            top_components=component_names[:6],
            top_concepts=top_concepts[:6],
            section_outline=section_outline,
            has_course=has_course,
        ))

    return materials


@router.get("/materials/{material_id}", response_model=MaterialOut)
def get_material(
    material_id: str,
    current_user: dict = Depends(_require_teacher),
) -> MaterialOut:
    """教材の詳細情報（ナレッジグラフ含む）を返す。

    Issue #128: list_materials と同じアクセスポリシーで判定する。
    """
    user_groups = get_user_group_ids(current_user["id"])

    params: dict = {"user_id": current_user["id"], "material_id": material_id}
    group_clause = "FALSE"
    course_mat_clause = "FALSE"
    doc_perm_clause = "FALSE"

    if user_groups:
        gph = ", ".join(f"CAST(:g_{i} AS uuid)" for i in range(len(user_groups)))
        for i, gid in enumerate(user_groups):
            params[f"g_{i}"] = gid
        group_clause = f"(d.visibility = 'group' AND d.group_id IN ({gph}))"
        course_mat_clause = f"""
            d.source_path IN (
                SELECT (jsonb_array_elements(lc.data->'sources')->>'material_id')
                FROM learning_courses lc
                JOIN object_group_permissions cgp
                    ON cgp.object_type = 'course' AND cgp.object_id = lc.id
                JOIN group_members gm ON gm.group_id = cgp.group_id
                WHERE gm.user_id = CAST(:user_id AS uuid)
                  AND cgp.group_id IN ({gph})
                  AND cgp.permission IN ('viewer', 'editor')
                  AND lc.data IS NOT NULL
                  AND jsonb_typeof(lc.data->'sources') = 'array'
            )
        """
        # migration 044: 解析成果のグループ直接共有（object_group_permissions object_type='document' 経由）
        doc_perm_clause = f"""
            d.id::text IN (
                SELECT dgp.object_id
                FROM object_group_permissions dgp
                JOIN group_members gm ON gm.group_id = dgp.group_id
                WHERE dgp.object_type = 'document'
                  AND gm.user_id = CAST(:user_id AS uuid)
                  AND dgp.group_id IN ({gph})
                  AND dgp.permission IN ('viewer', 'editor')
            )
        """

    session = _pg_session()
    try:
        record = session.execute(
            sa_text(f"""
                SELECT d.source_path, d.filename, d.title, d.status, d.created_at, d.knowledge_graph,
                       COALESCE(d.visibility, 'private'), d.group_id,
                       COALESCE(d.chunk_count, cs.chunk_count, 0) AS chunk_count
                FROM documents d
                LEFT JOIN (
                    SELECT material_id, COUNT(*) AS chunk_count
                    FROM chunks
                    GROUP BY material_id
                ) cs ON cs.material_id = d.source_path
                WHERE d.source_path = :material_id
                  AND (
                      d.uploaded_by = CAST(:user_id AS uuid)
                      OR d.visibility = 'public'
                      OR {group_clause}
                      OR {course_mat_clause}
                      OR {doc_perm_clause}
                  )
                LIMIT 1
            """),
            params,
        ).fetchone()
        # Tier3-16: list_materials と同じ projector 導出で run を反映する
        # （従来 get_material は run を一切見ておらず、一覧と詳細の status が
        # 食い違うバグがあった。同一セッション内で1回だけ追加で投影する）。
        mstatus = status_projector.project_material_status(session, material_id) if record else None
    finally:
        session.close()

    if not record:
        raise HTTPException(status_code=404, detail="Material not found")

    kg = record[5] if record[5] else None

    status = record[3] or "uploaded"
    with _material_lock:
        if material_id in _material_status:
            status = _material_status[material_id].get("status", status)

    legacy_status = _legacy_material_status(mstatus)
    if legacy_status is not None:
        status = legacy_status

    uploaded_at = record[4].isoformat() if record[4] else ""
    return MaterialOut(
        material_id=record[0] or "",
        filename=record[1] or "",
        title=record[2] or "",
        status=status,
        uploaded_at=uploaded_at,
        chunk_count=int(record[8] or 0),
        knowledge_graph=kg,
        visibility=record[6] or "private",
        group_id=str(record[7]) if record[7] else None,
    )


@router.get("/materials/{material_id}/pdf")
def get_material_pdf(
    material_id: str,
    current_user: dict = Depends(_require_teacher),
) -> Response:
    """認証済みユーザーに教材PDFをプロキシ配信する。"""
    material = get_material(material_id, current_user)
    object_candidates = [
        f"uploads/{material_id}.pdf",
        material.filename,
        material.material_id,
    ]
    storage = get_storage_client()
    for object_name in object_candidates:
        if not object_name:
            continue
        try:
            pdf_bytes = storage.get_object("raw-papers", object_name)
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'inline; filename="{material_id}.pdf"',
                    "Cache-Control": "private, max-age=300",
                },
            )
        except Exception as exc:
            logger.debug(
                "PDF not found in MinIO: bucket=raw-papers object=%s error=%s",
                object_name, exc,
            )
            continue
    logger.warning("PDF object not found for material=%s candidates=%s", material_id, object_candidates)
    raise HTTPException(status_code=404, detail="PDF object not found")


@router.put("/materials/{material_id}/pdf")
def reupload_material_pdf(
    material_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """既存教材のPDFをMinIOへ再登録し、欠落したチャンクページ情報を補完する。

    MinIOへのPDF保存が欠落している旧教材の復元に使用する。
    """
    get_material(material_id, current_user)  # アクセス権確認

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    pdf_bytes = file.file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # ── テキスト照合（先頭3ページ vs DBの先頭チャンク群） ──────────────
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        new_text = " ".join(
            page.get_text().replace("\x00", "") for page in list(doc)[:3]
        )
        doc.close()
    except Exception:
        new_text = ""

    if new_text.strip():
        session = _pg_session()
        try:
            rows = session.execute(
                sa_text("""
                    SELECT text FROM chunks
                    WHERE material_id = :mid
                    ORDER BY chunk_index
                    LIMIT 5
                """),
                {"mid": material_id},
            ).fetchall()
        finally:
            session.close()

        existing_text = " ".join(r[0] or "" for r in rows)
        if existing_text.strip():
            similarity = _jaccard(_word_set(new_text), _word_set(existing_text))
            logger.info(
                "PDF re-upload similarity check: material=%s similarity=%.3f",
                material_id, similarity,
            )
            if similarity < 0.3:
                raise HTTPException(
                    status_code=409,
                    detail=f"アップロードされたPDFは登録済みの教材と一致しません（テキスト類似度: {similarity:.0%}）。元と同じPDFファイルを選択してください。",
                )

    pdf_object_name = f"uploads/{material_id}.pdf"
    try:
        get_storage_client().upload_pdf("raw-papers", pdf_object_name, pdf_bytes)
    except Exception:
        logger.exception("Failed to re-upload PDF for material %s", material_id)
        raise HTTPException(status_code=500, detail="PDF storage failed")

    page_info_updated = 0
    try:
        page_info_updated = _backfill_missing_chunk_pages_from_pdf(material_id, pdf_bytes)
    except Exception:
        logger.exception("Failed to backfill chunk page info for material %s", material_id)

    logger.info(
        "PDF re-uploaded for material=%s size=%d similarity_ok=True page_info_updated=%d",
        material_id,
        len(pdf_bytes),
        page_info_updated,
    )
    return {
        "material_id": material_id,
        "object_name": pdf_object_name,
        "page_info_updated": page_info_updated,
    }


@router.put("/materials/{material_id}/visibility")
def update_material_visibility(
    material_id: str,
    body: VisibilityUpdateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教材の開示範囲を更新する。"""
    if body.visibility not in ("public", "group", "private"):
        raise HTTPException(status_code=400, detail=f"Invalid visibility: {body.visibility}")
    if body.visibility == "group":
        if not body.group_id:
            raise HTTPException(status_code=400, detail="visibility='group' requires group_id")
        if not user_can_access_group(current_user["id"], body.group_id):
            raise HTTPException(status_code=403, detail="指定されたグループに参加していません")

    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE documents
                SET visibility = :visibility,
                    group_id = CAST(:group_id AS uuid),
                    updated_at = now()
                WHERE source_path = :material_id
                  AND uploaded_by = CAST(:user_id AS uuid)
                RETURNING id
            """),
            {
                "visibility": body.visibility,
                "group_id": body.group_id if body.visibility == "group" else None,
                "material_id": material_id,
                "user_id": current_user["id"],
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if not result:
        raise HTTPException(status_code=404, detail="Material not found")
    logger.info(
        "Material %s visibility=%s group=%s by user=%s",
        material_id, body.visibility, body.group_id, current_user["id"],
    )
    return {
        "material_id": material_id,
        "visibility": body.visibility,
        "group_id": body.group_id if body.visibility == "group" else None,
    }


@router.put("/courses/{course_id}/visibility")
def update_course_visibility(
    course_id: str,
    body: VisibilityUpdateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースの開示範囲を更新する。

    G1-1 是正: is_published は「現在 visibility='public' か」を常に正確に反映させる
    （旧実装は public 化のときだけ true を立て、group/private へ戻しても is_published が
    true のまま残っていた。学習者向け一覧・enroll・get_accessible_course_data は
    visibility も併せて見るため受講可能一覧への漏れは無かったが、教員向けの公開状態表示
    （コース管理タブ・G層 next-steps の「未公開」判定）が実態とずれていたため是正する）。
    is_template は「テンプレートとして作られたことがあるか」の意図を保つため、
    従来どおり public 化時のみ true を立て、離脱時にリセットはしない。
    """
    if body.visibility not in ("public", "group", "private"):
        raise HTTPException(status_code=400, detail=f"Invalid visibility: {body.visibility}")
    if body.visibility == "group":
        if not body.group_id:
            raise HTTPException(status_code=400, detail="visibility='group' requires group_id")
        if not user_can_access_group(current_user["id"], body.group_id):
            raise HTTPException(status_code=403, detail="指定されたグループに参加していません")

    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                UPDATE learning_courses
                SET visibility = :visibility,
                    group_id = CAST(:group_id AS uuid),
                    is_published = (:visibility = 'public'),
                    is_template = CASE WHEN :visibility = 'public' THEN true ELSE is_template END,
                    updated_at = now()
                WHERE id = :course_id AND user_id = CAST(:user_id AS uuid)
                RETURNING id
            """),
            {
                "visibility": body.visibility,
                "group_id": body.group_id if body.visibility == "group" else None,
                "course_id": course_id,
                "user_id": current_user["id"],
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if not result:
        raise HTTPException(status_code=404, detail="Course not found")
    logger.info(
        "Course %s visibility=%s group=%s by user=%s",
        course_id, body.visibility, body.group_id, current_user["id"],
    )
    return {
        "course_id": course_id,
        "visibility": body.visibility,
        "group_id": body.group_id if body.visibility == "group" else None,
    }


# ---------------------------------------------------------------------------
# Material Deletion
# ---------------------------------------------------------------------------


def _versioning_collect_recipients(object_type: str, object_id: str) -> list[str]:
    """物理削除の前に V層の通知宛先（グループ共有先 + 購読者）を集める（best-effort）。

    削除でグループ権限が消える前に集めておく（消えた後は recipients_for が空になる）。
    """
    try:
        from core.versioning import notifications as _vn, subscriptions as _vs
        ids = list(_vn.recipients_for(object_type, str(object_id)))
        ids += _vs.subscriber_ids(object_type, str(object_id))
        return ids
    except Exception:  # noqa: BLE001 — 収集失敗は削除本体を止めない
        logger.debug("versioning recipient collection skipped: %s %s", object_type, object_id, exc_info=True)
        return []


def _versioning_teardown_after_delete(targets, *, primary_recipients=None, actor=None) -> None:
    """既存の即時削除（delete_material / delete_course）後の V層あと片付け（best-effort）。

    共有版・ピン・通知を掃除し state を 'purged' 墓標にして、購読者へ 'deleted' を送る。
    これにより手動削除でも幽霊ピン・陳腐化通知・active のままの state を残さない（orphan gap 解消）。
    targets: (object_type, object_id) のリスト。primary_recipients は targets[0] のみに適用する。
    """
    try:
        from core.versioning import deletion as _vdeletion
    except Exception:  # noqa: BLE001
        return
    for i, (object_type, object_id) in enumerate(targets):
        try:
            _vdeletion.teardown_versioning(
                object_type, str(object_id),
                extra_recipients=(primary_recipients if i == 0 else None),
                actor=actor,
            )
        except Exception:  # noqa: BLE001 — 片付け失敗は削除本体を無効化しない
            logger.debug("versioning teardown skipped: %s %s", object_type, object_id, exc_info=True)


@router.delete("/materials/{material_id}")
def delete_material(
    material_id: str,
    body: DeleteConfirmRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教材を削除する。紐づくコースも同時に削除される。

    confirm_name がタイトルと一致しない場合は 400 を返す。
    """
    session = _pg_session()
    try:
        # 1) 教材の存在確認 & タイトル照合
        doc = session.execute(
            sa_text("""
                SELECT id, title, filename, source_path
                FROM documents
                WHERE source_path = :material_id
                  AND uploaded_by = CAST(:user_id AS uuid)
                LIMIT 1
            """),
            {"material_id": material_id, "user_id": current_user["id"]},
        ).fetchone()

        if not doc:
            raise HTTPException(status_code=404, detail="Material not found")

        doc_id = doc[0]
        doc_title = doc[1] or doc[2] or ""

        if body.confirm_name != doc_title:
            raise HTTPException(
                status_code=400,
                detail="確認用の名前が一致しません。正確な教材名を入力してください。",
            )

        # V層（migration 037）: 削除でグループ権限が消える前に通知宛先を集めておく
        doc_recipients = _versioning_collect_recipients("document", doc_id)

        # 2) この教材を sources に含むコースを特定して削除
        course_rows = session.execute(
            sa_text("""
                SELECT id FROM learning_courses
                WHERE user_id = CAST(:user_id AS uuid)
            """),
            {"user_id": current_user["id"]},
        ).fetchall()

        deleted_course_ids: list[str] = []
        for row in course_rows:
            course_id = row[0]
            course_data_row = session.execute(
                sa_text("SELECT data FROM learning_courses WHERE id = :cid"),
                {"cid": course_id},
            ).fetchone()
            if not course_data_row or not course_data_row[0]:
                continue
            data = course_data_row[0] if isinstance(course_data_row[0], dict) else json.loads(course_data_row[0])
            sources = course_sources(data)
            linked = any(
                s.get("material_id") == material_id for s in sources if isinstance(s, dict)
            )
            if linked:
                # 関連する学習チャット履歴を削除
                session.execute(
                    sa_text("DELETE FROM learning_chat_history WHERE course_id = :cid"),
                    {"cid": course_id},
                )
                # object_group_permissions は course_id への FK が無いポリモーフィック
                # テーブルなので明示削除する（孤児防止。migration 044）。
                session.execute(
                    sa_text(
                        "DELETE FROM object_group_permissions "
                        "WHERE object_type = 'course' AND object_id = :cid"
                    ),
                    {"cid": course_id},
                )
                # コース削除
                session.execute(
                    sa_text("DELETE FROM learning_courses WHERE id = :cid"),
                    {"cid": course_id},
                )
                deleted_course_ids.append(course_id)

        # 3) チャンク削除
        session.execute(
            sa_text("DELETE FROM chunks WHERE document_id = :doc_id"),
            {"doc_id": doc_id},
        )

        # object_group_permissions は document_id への FK が無いポリモーフィック
        # テーブルなので明示削除する（孤児防止。migration 044）。
        session.execute(
            sa_text(
                "DELETE FROM object_group_permissions "
                "WHERE object_type = 'document' AND object_id = CAST(:doc_id AS uuid)::text"
            ),
            {"doc_id": doc_id},
        )

        # W層 同一性リンク（migration 048）の instance 側も document_id への FK が無い
        # ポリモーフィック行なので明示削除する（孤児防止。_purge_document と同じ
        # orphan gap パターン。document_id は UUID / material_id 両形で書かれ得るため
        # 両方を見る）。
        session.execute(
            sa_text(
                "DELETE FROM element_identity_links "
                "WHERE instance_document_id IN (CAST(:doc_id AS uuid)::text, :material_id)"
            ),
            {"doc_id": doc_id, "material_id": material_id},
        )

        # W層 対話セッション + 候補注釈（migration 049）の scope='document' 行も document_id への
        # FK が無いポリモーフィック行なので明示削除する（_purge_document と同じ orphan gap
        # パターン。scope='domain' 行は document_id が NULL のため対象外・L層のライフサイクルに従う）。
        session.execute(
            sa_text(
                "DELETE FROM element_annotations "
                "WHERE document_id IN (CAST(:doc_id AS uuid)::text, :material_id)"
            ),
            {"doc_id": doc_id, "material_id": material_id},
        )
        session.execute(
            sa_text(
                "DELETE FROM deliberation_sessions "
                "WHERE document_id IN (CAST(:doc_id AS uuid)::text, :material_id)"
            ),
            {"doc_id": doc_id, "material_id": material_id},
        )

        # Track A（hierarchical_context_explanation_design.md §5.2）の二層説明台帳:
        # document_id は element_annotations 等と異なり documents.id に準拠する
        # UUID 列（FK 無し）なので material_id 形は不要（孤児防止。_purge_document と同じ）。
        session.execute(
            sa_text("DELETE FROM element_explanations WHERE document_id = CAST(:doc_id AS uuid)"),
            {"doc_id": doc_id},
        )

        # 4) ドキュメント削除
        session.execute(
            sa_text("DELETE FROM documents WHERE id = :doc_id"),
            {"doc_id": doc_id},
        )

        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info(
        "Material %s (%s) deleted by user=%s, cascade-deleted courses: %s",
        material_id, doc_title, current_user["id"], deleted_course_ids,
    )
    # V層（migration 037）: 教材とその巻き添えコースの共有版状態を掃除し購読者へ通知する
    _versioning_teardown_after_delete(
        [("document", doc_id)] + [("course", cid) for cid in deleted_course_ids],
        primary_recipients=doc_recipients,
        actor=current_user["id"],
    )
    return {
        "material_id": material_id,
        "deleted": True,
        "deleted_courses": deleted_course_ids,
    }


# ---------------------------------------------------------------------------
# Background Task Polling (Issue #63)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}", response_model=BackgroundTaskOut)
def get_task_status(
    task_id: str,
    current_user: dict = Depends(_require_teacher),
) -> BackgroundTaskOut:
    """バックグラウンドタスクのステータスを返す。ポーリング用エンドポイント。"""
    task = get_background_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return BackgroundTaskOut(**task)


# ---------------------------------------------------------------------------
# Course Builder Chat
# ---------------------------------------------------------------------------

_COURSE_BUILDER_SYSTEM_PROMPT = """あなたは大学教員が学習コース（シラバス）を設計するのを支援するAIアシスタントです。

**あなたの役割:**
- 教員の要望に基づいて、体系的な学習コースの構成案を段階的に作成・改善する
- 不足している前提知識や学習順序の問題を指摘する
- 各章・トピックが論理的に繋がるよう構成を提案する

**【最重要】ソース文献への厳密な準拠:**
- 提案するすべての章・トピック・概念は、提供された教材の記述内容に基づかなければならない
- 教材に記載されていないトピックを「一般的だから」「基礎的だから」という理由で追加してはならない
- 教材の内容を超えた一般知識に基づく補足は、明示的に「教材外の補足」として区別すること
- 提供された理論コンポーネント・依存関係グラフ・根拠Claimを最大限活用してコースを設計すること

**【重要】段階的構造化の原則:**
- コースの章・トピック候補は `theory_components` を主に使用する（各コンポーネントの name / summary / inputs / outputs / preconditions を参照）
- 学習順序は `theory_component_graphs` の依存関係（edges）を優先する（PDFの出現順よりも依存関係を重視）
- `theory_claims` は各コンポーネントの根拠確認・evidence検証に使う
- `chunks`（原文抜粋）は説明補助として使い、コース設計の主判断材料にしない
- 前提知識が必要な概念は、その前提となる概念より後の章・トピックに配置する（依存関係に基づいたシラバス）
- 初学者がその分野に初めて触れることを想定し、専門用語は最初に出現する章で定義・説明するよう構成する

**【注意】ナレッジグラフの扱い:**
- `documents.knowledge_graph` はAgentパイプライン未実行の旧教材のみに利用するfallbackである
- Agent解析済み教材では `theory_components` / `theory_component_graphs` を優先し、ナレッジグラフは参照しない

**コース構成のJSONスキーマ (course_draft):**
生成するコース構成案は以下の形式に従ってください:
{
  "title": "コースタイトル",
  "domain": "コースの専門分野（例: 素粒子物理学、経済学、機械学習など）",
  "target_audience": "対象者（例: 物理学専攻の大学院1年生）",
  "goal": "到達目標",
  "prerequisites": ["前提知識1", "前提知識2"],
  "chapters": [
    {
      "title": "章タイトル",
      "topics": [
        {"title": "トピック名", "prerequisites": []},
        {"title": "トピック名", "prerequisites": ["前のトピック名"]}
      ]
    }
  ],
  "concepts": [
    {"name": "概念名", "children": ["子概念1", "子概念2"]}
  ],
  "sources": []
}

**対話の進め方:**
1. まず教員の要望（テーマ、対象者、使用教材等）をヒアリングする
2. 提供された理論コンポーネントと依存関係グラフを精査し、教材の範囲と構成を把握する
3. theory_components を章・トピック候補として使い、theory_component_graphs の依存関係で順序を決定する
4. 教員のフィードバックに基づいて構成案を改善する
5. 前提知識の不足や学習順序の問題があれば指摘する

**重要なルール:**
- 応答は必ず日本語で行う
- コース構成案を提示・更新する場合は、応答テキストの最後に `---COURSE_DRAFT_JSON---` という区切り文字の後にJSONを出力する
- JSONは上記スキーマに従った valid JSON であること
- 構成案がまだ不完全な場合でも、現時点の案を出力する
- 教員が単なる質問をしている場合（構成変更を伴わない場合）は区切り文字とJSONを出力しない
- topics[].prerequisites には、そのトピックを学ぶ前に習得しておくべき**同コース内の**トピックのタイトルを列挙する
  - 例: 第2章のトピックは第1章のトピックタイトルを prerequisites に入れる
  - 最初のトピックや前提知識不要なトピックは prerequisites を空配列 [] にする
- domain フィールドは教材の分野情報（コンポーネントや旧ナレッジグラフの「**分野:**」）から引き継ぐこと
  - 教材の分野情報がなければ、コースの内容を踏まえて適切な専門分野名を設定する
- sources フィールドは常に空配列 [] のままにすること（教材はシステムが自動的に設定する）"""


_COURSE_DRAFT_MARKER_RE = re.compile(
    r"(?:-{3,}\s*)?COURSE_DRAFT_JSON(?:\s*-{3,})?\s*:?\s*",
    re.IGNORECASE,
)


def _extract_course_draft_from_answer(raw_answer: str) -> tuple[str, dict | None]:
    """LLM応答から course_draft JSON を分離する。

    モデルが指示通りの `---COURSE_DRAFT_JSON---` だけでなく、ダッシュなしの
    `COURSE_DRAFT_JSON` や fenced code block を返した場合もプレビューへ渡せるよう
    にする。パースに失敗した場合も、会話本文からマーカー以降は除去する。
    """
    marker = _COURSE_DRAFT_MARKER_RE.search(raw_answer or "")
    if not marker:
        return raw_answer, None

    answer = raw_answer[: marker.start()].strip()
    json_part = raw_answer[marker.end() :].strip()

    if json_part.startswith("```"):
        json_part = json_part.split("\n", 1)[1] if "\n" in json_part else json_part[3:]
        if "```" in json_part:
            json_part = json_part.split("```", 1)[0]

    json_part = json_part.strip()
    if json_part.lower().startswith("json"):
        json_part = json_part[4:].strip()

    try:
        course_draft, _ = json.JSONDecoder().raw_decode(json_part)
    except Exception:
        logger.warning("Failed to parse course_draft JSON: %s", json_part[:200])
        return answer, None

    if not isinstance(course_draft, dict):
        logger.warning("Parsed course_draft JSON is not an object: %s", type(course_draft).__name__)
        return answer, None
    return answer, course_draft


# ---------------------------------------------------------------------------
# Course Builder: Material Context Builder
# ---------------------------------------------------------------------------

# サイズ制限（1教材あたり）
_MAX_CHUNK_CHARS_PER_MATERIAL = 4000
_MAX_COMPONENTS_PER_MATERIAL = 40
_MAX_CLAIMS_PER_MATERIAL = 80
_MAX_GRAPH_EDGES_PER_MATERIAL = 80


def _build_material_context(
    material_ids: list[str],
    pg_session_factory=None,
) -> str | None:
    """選択された教材のAgent解析済み理論コンポーネントを主入力としてコンテキスト文字列を構築する。

    主入力: theory_components / theory_component_graphs / theory_claims
    補助入力: chunks (原文抜粋) / document_analysis_runs._artifacts.document_structure
    fallback: documents.knowledge_graph (Agent未実行の旧教材のみ)

    Returns None if no usable context could be built.
    """
    if not material_ids:
        return None

    _get_pg = pg_session_factory or _pg_session
    session = _get_pg()
    try:
        placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
        params: dict = {f"mid_{i}": mid for i, mid in enumerate(material_ids)}

        # --- 1) ドキュメントメタデータ取得（UUID と source_path の対応も取得）---
        doc_rows = session.execute(
            sa_text(f"""
                SELECT source_path, title, filename, id::text AS doc_uuid,
                       status, knowledge_graph
                FROM documents
                WHERE source_path IN ({placeholders})
            """),
            params,
        ).fetchall()

        if not doc_rows:
            return None

        # doc_uuid → source_path マッピング
        uuid_to_mid: dict[str, str] = {row[3]: row[0] for row in doc_rows}
        doc_uuids = list(uuid_to_mid.keys())
        uuid_placeholders = ", ".join(f":uuid_{i}" for i in range(len(doc_uuids)))
        uuid_params: dict = {f"uuid_{i}": uid for i, uid in enumerate(doc_uuids)}

        # --- 2) theory_components (主入力) ---
        component_rows = session.execute(
            sa_text(f"""
                SELECT id::text, document_id, name, component_type, component_type_text,
                       summary, inputs, outputs, preconditions, cautions,
                       source_chunks, evidence_claims, review_status, maturity_level
                FROM theory_components
                WHERE document_id IN ({uuid_placeholders})
                ORDER BY
                    CASE review_status WHEN 'teacher_reviewed' THEN 0 ELSE 1 END,
                    CASE maturity_level
                        WHEN 'peer_reviewed' THEN 0
                        WHEN 'textbook' THEN 1
                        WHEN 'paper_claim' THEN 2
                        ELSE 3
                    END
            """),
            uuid_params,
        ).fetchall() if doc_uuids else []

        # --- 3) theory_component_graphs (主入力) ---
        graph_rows = session.execute(
            sa_text(f"""
                SELECT document_id, graph_json
                FROM theory_component_graphs
                WHERE document_id IN ({uuid_placeholders})
            """),
            uuid_params,
        ).fetchall() if doc_uuids else []

        # --- 4) theory_claims (補助入力) ---
        claim_rows = session.execute(
            sa_text(f"""
                SELECT id::text, document_id, claim_type, text, normalized_text,
                       source_scope, evidence_text, support_status, review_status
                FROM theory_claims
                WHERE document_id IN ({uuid_placeholders})
                ORDER BY
                    CASE review_status WHEN 'teacher_reviewed' THEN 0 ELSE 1 END,
                    CASE support_status WHEN 'source_backed' THEN 0 ELSE 1 END
            """),
            uuid_params,
        ).fetchall() if doc_uuids else []

        # --- 5) chunks (補助入力: 原文抜粋) ---
        chunk_rows = session.execute(
            sa_text(f"""
                SELECT material_id, chunk_index, section_id, text, page_start, page_end
                FROM chunks
                WHERE material_id IN ({placeholders})
                ORDER BY material_id, chunk_index
            """),
            params,
        ).fetchall()

        # --- 6) document_analysis_runs: 完了状態確認 + document_structure ---
        analysis_rows = session.execute(
            sa_text(f"""
                SELECT document_id, status, stage_outputs
                FROM document_analysis_runs
                WHERE document_id IN ({uuid_placeholders})
                ORDER BY updated_at DESC
            """),
            uuid_params,
        ).fetchall() if doc_uuids else []

    finally:
        session.close()

    # 完了済み analysis run のマップ (doc_uuid → row)
    analysis_by_uuid: dict[str, object] = {}
    for row in analysis_rows:
        uid = row[0]
        if uid not in analysis_by_uuid:
            analysis_by_uuid[uid] = row

    # --- 7) コンテキスト文字列を組み立て ---
    sections: list[str] = []
    sections.append(
        "## Agent解析済み教材コンテキスト\n"
        "以下は選択された教材からAgentパイプラインが生成した理論コンポーネント情報です。"
        "コース設計はこの内容に基づいて行ってください。\n"
    )

    for doc in doc_rows:
        mid = doc[0] or ""
        title = doc[1] or doc[2] or ""
        doc_uuid = doc[3] or ""
        kg = doc[5] if doc[5] and isinstance(doc[5], dict) else {}

        sections.append(f"### 教材: {title}")

        # Agent完了状態チェック
        analysis_run = analysis_by_uuid.get(doc_uuid)
        pipeline_complete = analysis_run is not None and analysis_run[1] == "completed"

        doc_components = [r for r in component_rows if r[1] == doc_uuid]
        doc_graphs = [r for r in graph_rows if r[0] == doc_uuid]
        has_agent_data = bool(doc_components or doc_graphs)

        if not has_agent_data and not pipeline_complete:
            sections.append(
                "> **警告:** この教材はAgentパイプラインが未完了のため、"
                "コース構築に十分な解析情報がありません。"
                "教材のアップロード後しばらく待ってから再試行してください。"
            )

        # ---- 主入力: 理論コンポーネント ----
        if doc_components:
            sections.append("#### 理論コンポーネント")
            for comp in doc_components[:_MAX_COMPONENTS_PER_MATERIAL]:
                comp_id = comp[0]
                name = comp[2] or ""
                ctype = comp[4] or comp[3] or ""
                summary = comp[5] or ""
                inputs = comp[6] if isinstance(comp[6], list) else []
                outputs = comp[7] if isinstance(comp[7], list) else []
                preconditions = comp[8] if isinstance(comp[8], list) else []
                cautions = comp[9] if isinstance(comp[9], list) else []
                review = comp[12] or ""
                maturity = comp[13] or ""

                lines = [f"- Component ID: {comp_id}"]
                lines.append(f"  Name: {name}")
                if ctype:
                    lines.append(f"  Type: {ctype}")
                if summary:
                    lines.append(f"  Summary: {summary}")
                if inputs:
                    lines.append(f"  Inputs: {', '.join(str(x) for x in inputs[:5])}")
                if outputs:
                    lines.append(f"  Outputs: {', '.join(str(x) for x in outputs[:5])}")
                if preconditions:
                    lines.append(f"  Preconditions: {', '.join(str(x) for x in preconditions[:5])}")
                if cautions:
                    lines.append(f"  Cautions: {', '.join(str(x) for x in cautions[:3])}")
                if review:
                    lines.append(f"  Review: {review}")
                if maturity:
                    lines.append(f"  Maturity: {maturity}")
                sections.append("\n".join(lines))

        # ---- 主入力: コンポーネント依存関係グラフ ----
        if doc_graphs:
            graph_json = doc_graphs[0][1] if isinstance(doc_graphs[0][1], dict) else {}
            edges = graph_json.get("edges", [])
            if edges:
                sections.append("#### コンポーネント依存関係")
                for edge in edges[:_MAX_GRAPH_EDGES_PER_MATERIAL]:
                    if not isinstance(edge, dict):
                        continue
                    src = edge.get("source", edge.get("from", ""))
                    tgt = edge.get("target", edge.get("to", ""))
                    etype = edge.get("type", edge.get("relation", ""))
                    reason = edge.get("reason", edge.get("label", ""))
                    line = f"- {src} -> {tgt}"
                    if etype:
                        line += f"  (Type: {etype})"
                    if reason:
                        line += f"  Reason: {reason}"
                    sections.append(line)

        # ---- 補助入力: 根拠Claim ----
        doc_claims = [r for r in claim_rows if r[1] == doc_uuid]
        if doc_claims:
            sections.append("#### 根拠Claim")
            for claim in doc_claims[:_MAX_CLAIMS_PER_MATERIAL]:
                claim_id = claim[0]
                ctype = claim[2] or ""
                text = claim[3] or ""
                evidence = claim[6] or ""
                support = claim[7] or ""
                lines = [f"- Claim ID: {claim_id}"]
                if ctype:
                    lines.append(f"  Type: {ctype}")
                if text:
                    lines.append(f"  Text: {text[:200]}")
                if evidence:
                    lines.append(f"  Evidence: {evidence[:150]}")
                if support:
                    lines.append(f"  Support: {support}")
                sections.append("\n".join(lines))

        # ---- 補助入力: 原文抜粋 ----
        material_chunks = [r for r in chunk_rows if r[0] == mid]
        if material_chunks:
            sections.append("#### 原文抜粋")
            char_budget = _MAX_CHUNK_CHARS_PER_MATERIAL
            for chunk in material_chunks:
                if char_budget <= 0:
                    sections.append("... (以降省略)")
                    break
                idx = chunk[1]
                section_id = chunk[2] or ""
                text = chunk[3] or ""
                page_start = chunk[4]
                page_end = chunk[5]
                header = f"[Chunk {idx}"
                if section_id:
                    header += f" / section {section_id}"
                if page_start is not None:
                    page_info = f"page {page_start}"
                    if page_end is not None and page_end != page_start:
                        page_info += f"-{page_end}"
                    header += f" / {page_info}"
                header += "]"
                snippet = text[:char_budget]
                if len(text) > char_budget:
                    snippet += "..."
                sections.append(f"{header}\n{snippet}")
                char_budget -= len(snippet)

        # ---- 補助入力: 文書構造 (document_structure) ----
        if analysis_run:
            stage_outputs = analysis_run[2] if isinstance(analysis_run[2], dict) else {}
            doc_structure = (stage_outputs.get("_artifacts") or {}).get("document_structure")
            if doc_structure and isinstance(doc_structure, dict):
                struct_sections = doc_structure.get("sections") or doc_structure.get("blocks") or []
                if struct_sections:
                    sections.append("#### 文書構造（セクション一覧）")
                    for sec in struct_sections[:20]:
                        if not isinstance(sec, dict):
                            continue
                        sec_title = sec.get("title") or sec.get("heading") or sec.get("label") or ""
                        if sec_title:
                            sections.append(f"- {sec_title}")

        # ---- fallback: 旧 knowledge_graph (Agent未実行の場合のみ) ----
        if not has_agent_data:
            logger.warning(
                "Course builder falling back to legacy documents.knowledge_graph "
                "because no agent components were found for material %s",
                mid,
            )
            if kg.get("summary"):
                sections.append(f"**概要 (legacy):** {kg['summary']}")
            if kg.get("domain"):
                sections.append(f"**分野:** {kg['domain']}")
            concepts = kg.get("concepts", [])
            if concepts:
                concept_lines = []
                for c in concepts:
                    name = c.get("name", "") if isinstance(c, dict) else str(c)
                    desc = c.get("description", "") if isinstance(c, dict) else ""
                    ctype = c.get("type", "") if isinstance(c, dict) else ""
                    line = f"- {name}"
                    if ctype:
                        line += f" ({ctype})"
                    if desc:
                        line += f": {desc}"
                    concept_lines.append(line)
                sections.append("**主要概念 (legacy):**\n" + "\n".join(concept_lines))

        sections.append("")  # blank line separator

    return "\n".join(sections)


class _CourseBuilderChatResponseOut(CourseBuilderChatResponse):
    """course_builder_chat レスポンス拡張（正本: assistant_common_infra_design.md §4/§6）。

    ``schemas.CourseBuilderChatResponse`` 自体はファイル所有権の都合上変更せず、
    ここでサブクラス化して ``degraded``（LLM 縮退）/ ``session_saved``
    （save_cb_session 正直化）を追加する。
    """

    degraded: bool = False
    session_saved: bool = True


# コースビルダーチャットの日次 LLM コール上限（正本: core/llm_worker/cost_gate.py の
# 共通 CostGate、day-only）。プロセスローカル・in-memory カウンタ
# （設計書 I6: 複数ワーカーで実効上限が緩む・再起動でリセットされる制約は許容）。
_course_builder_cost_gate = CostGate()

_COURSE_BUILDER_DEGRADED_MESSAGE = (
    "AI 応答を生成できませんでした。しばらくしてからもう一度お試しください。"
)


@router.post(
    "/course-builder/chat",
    response_model=_CourseBuilderChatResponseOut,
)
def course_builder_chat(
    body: CourseBuilderChatRequest,
    current_user: dict = Depends(_require_teacher),
) -> _CourseBuilderChatResponseOut:
    """教員がAIと対話しながらコースを設計するエンドポイント。"""
    settings = get_settings()
    daily_cap = int(getattr(settings, "course_builder_max_calls_per_day", 100) or 0)
    if not _course_builder_cost_gate.check_and_count(
        daily_limit=daily_cap, daily_key=(today_str(), current_user["id"])
    ):
        raise HTTPException(
            status_code=429,
            detail="本日のAI呼び出し回数の上限に達しました。明日以降に再度お試しください。",
        )

    messages: list[dict] = [
        {"role": "system", "content": _COURSE_BUILDER_SYSTEM_PROMPT},
    ]

    # 選択教材のナレッジグラフ・チャンクテキストを含む詳細コンテキストを注入
    if body.selected_material_ids:
        try:
            material_context = _build_material_context(body.selected_material_ids)
            if material_context:
                messages.append({
                    "role": "user",
                    "content": material_context
                        + "\n\n上記の教材内容に基づいてコースを設計してください。"
                        "教材に記載されている内容の範囲内で、段階的に学べるコース構成を提案してください。",
                })
                messages.append({
                    "role": "assistant",
                    "content": "承知しました。提供された教材の内容を精査し、"
                        "教材の記述に基づいたコース設計を支援します。",
                })
        except Exception:
            logger.warning("Failed to load selected materials context for course builder", exc_info=True)

    # 会話履歴のウィンドウ化（設計書 §2-2）。フロント（admin.js::sendCourseChat）が
    # course_draft を JSON 化して履歴の先頭に user/assistant 疑似ターン（draftContext /
    # draftAck の2件）を注入するため、head_keep=2 でこの2件を保護する。
    windowed_history = window_history(body.history, max_messages=20, max_chars=4000, head_keep=2)
    for turn in windowed_history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": body.message})

    degraded = False
    course_draft: dict | None = None
    try:
        with usage_context("admin:course_builder", user_id=current_user["id"]):
            raw_answer = generate_text(
                messages=messages,
                temperature=0.4,
                model=resolve_model("course_builder_llm_model", fallback="analysis"),
            )
    except Exception:
        logger.exception("Course builder chat LLM call failed")
        degraded = True
        answer = _COURSE_BUILDER_DEGRADED_MESSAGE
    else:
        answer, course_draft = _extract_course_draft_from_answer(raw_answer)

        # 選択教材の正しい material_id を確定的に course_draft["sources"] に注入する
        if course_draft is not None and body.selected_material_ids:
            try:
                pg_session = _pg_session()
                try:
                    placeholders = ", ".join(f":mid_{i}" for i in range(len(body.selected_material_ids)))
                    params = {}
                    for i, mid in enumerate(body.selected_material_ids):
                        params[f"mid_{i}"] = mid
                    records = pg_session.execute(
                        sa_text(f"""
                            SELECT source_path, title, filename
                            FROM documents
                            WHERE source_path IN ({placeholders}) AND status = 'completed'
                        """),
                        params,
                    ).fetchall()
                finally:
                    pg_session.close()

                sources = []
                for r in records:
                    sources.append({
                        "material_id": r[0] or "",
                        "title": r[1] or r[2] or "",
                        "subtitle": "",
                    })
                course_draft["sources"] = sources
            except Exception:
                logger.warning("Failed to inject material sources into course_draft", exc_info=True)

    logger.info(
        "Course builder chat for user=%s, draft=%s, degraded=%s",
        current_user["id"],
        "yes" if course_draft else "no",
        degraded,
    )

    # セッション保存は履歴を丸ごと（ウィンドウ化しない）保存する。degraded ターンも
    # 通常どおり保存する（設計書 I4: 情報を落とさない）。
    session_saved = True
    if body.session_id:
        updated_history = body.history + [
            {"role": "user", "content": body.message},
            {"role": "assistant", "content": answer},
        ]
        session_saved = save_cb_session(current_user["id"], body.session_id, updated_history, course_draft)

    return _CourseBuilderChatResponseOut(
        answer=answer,
        course_draft=course_draft,
        degraded=degraded,
        session_saved=session_saved,
    )


# ---------------------------------------------------------------------------
# Course Builder Sessions
# ---------------------------------------------------------------------------

_FORBIDDEN_CHARS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _generate_session_display_name(source_file_name: str | None, title: str) -> str:
    """セッション表示名を <source_file>_<YYYYMMDD_HHMM>_<title_short> 形式で生成する。"""
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    title_str = (title or "").strip()
    if not title_str:
        title_str = "新規コース設計"
    title_short = title_str[:20] if len(title_str) > 20 else title_str
    title_short = _FORBIDDEN_CHARS_RE.sub("_", title_short).strip("_")

    if source_file_name:
        src = _FORBIDDEN_CHARS_RE.sub("_", source_file_name.strip()).strip("_")
        src = src[:40] if len(src) > 40 else src
        return f"{src}_{now_str}_{title_short}"
    return f"{now_str}_{title_short}"


@router.post("/course-builder/sessions", response_model=CourseBuilderSessionOut, status_code=201)
def create_cb_session(
    body: CourseBuilderSessionCreate,
    current_user: dict = Depends(_require_teacher),
) -> CourseBuilderSessionOut:
    """コース構築セッションを新規作成する。"""
    session_id = str(uuid.uuid4())[:12]
    title = body.title or ""
    display_name = body.display_name or _generate_session_display_name(body.source_file_name, title)
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                INSERT INTO course_builder_sessions
                    (id, user_id, title, history, course_draft,
                     source_file_name, display_name, status)
                VALUES (:session_id, CAST(:user_id AS uuid), :title, '[]', null,
                        :source_file_name, :display_name, 'draft')
            """),
            {
                "session_id": session_id,
                "user_id": current_user["id"],
                "title": title,
                "source_file_name": body.source_file_name,
                "display_name": display_name,
            },
        )
        row = session.execute(
            sa_text("SELECT created_at, updated_at FROM course_builder_sessions WHERE id = :id"),
            {"id": session_id},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    created_at = row[0].isoformat() if row and row[0] else ""
    updated_at = row[1].isoformat() if row and row[1] else ""
    logger.info("Created course builder session %s for user=%s", session_id, current_user["id"])
    return CourseBuilderSessionOut(
        session_id=session_id, title=title,
        display_name=display_name, source_file_name=body.source_file_name,
        status="draft", published_course_id=None,
        created_at=created_at, updated_at=updated_at,
    )


@router.get("/course-builder/sessions", response_model=list[CourseBuilderSessionOut])
def list_cb_sessions(
    current_user: dict = Depends(_require_teacher),
) -> list[CourseBuilderSessionOut]:
    """コース構築セッション一覧を返す（更新日時の降順）。"""
    session = _pg_session()
    try:
        records = session.execute(
            sa_text("""
                SELECT id, title, created_at, updated_at,
                       source_file_name, display_name,
                       COALESCE(status, 'draft'), published_course_id
                FROM course_builder_sessions
                WHERE user_id = CAST(:user_id AS uuid)
                ORDER BY updated_at DESC
            """),
            {"user_id": current_user["id"]},
        ).fetchall()
    finally:
        session.close()
    return [
        CourseBuilderSessionOut(
            session_id=r[0],
            title=r[1] or "",
            created_at=r[2].isoformat() if r[2] else "",
            updated_at=r[3].isoformat() if r[3] else "",
            source_file_name=r[4],
            display_name=r[5],
            status=r[6] or "draft",
            published_course_id=r[7],
        )
        for r in records
    ]


@router.get("/course-builder/sessions/{session_id}")
def get_cb_session(
    session_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コース構築セッションの詳細（履歴・draft含む）を返す。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT id, title, history, course_draft, created_at, updated_at,
                       source_file_name, display_name,
                       COALESCE(status, 'draft'), published_course_id
                FROM course_builder_sessions
                WHERE id = :session_id AND user_id = CAST(:user_id AS uuid)
            """),
            {"session_id": session_id, "user_id": current_user["id"]},
        ).fetchone()
    finally:
        session.close()

    if not record:
        raise HTTPException(status_code=404, detail="Session not found")

    history = record[2] if isinstance(record[2], list) else (
        json.loads(record[2]) if record[2] else []
    )
    course_draft = record[3] if isinstance(record[3], dict) else (
        json.loads(record[3]) if record[3] else None
    )

    return {
        "session_id": record[0],
        "title": record[1] or "",
        "history": history,
        "course_draft": course_draft,
        "created_at": record[4].isoformat() if record[4] else "",
        "updated_at": record[5].isoformat() if record[5] else "",
        "source_file_name": record[6],
        "display_name": record[7],
        "status": record[8] or "draft",
        "published_course_id": record[9],
    }


@router.put("/course-builder/sessions/{session_id}", response_model=CourseBuilderSessionOut)
def update_cb_session(
    session_id: str,
    body: CourseBuilderSessionUpdate,
    current_user: dict = Depends(_require_teacher),
) -> CourseBuilderSessionOut:
    """コース構築セッションのタイトル・履歴・draft を更新する。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT title, history, course_draft, created_at,
                       source_file_name, display_name,
                       COALESCE(status, 'draft'), published_course_id
                FROM course_builder_sessions
                WHERE id = :session_id AND user_id = CAST(:user_id AS uuid)
            """),
            {"session_id": session_id, "user_id": current_user["id"]},
        ).fetchone()
        if not record:
            raise HTTPException(status_code=404, detail="Session not found")

        new_title = body.title if body.title is not None else (record[0] or "")
        new_history = (
            json.dumps(body.history, ensure_ascii=False)
            if body.history is not None
            else json.dumps(
                record[1] if isinstance(record[1], list) else (json.loads(record[1]) if record[1] else []),
                ensure_ascii=False,
            )
        )
        new_draft = (
            json.dumps(body.course_draft, ensure_ascii=False)
            if body.course_draft is not None
            else (json.dumps(record[2]) if record[2] else None)
        )
        new_source_file_name = body.source_file_name if body.source_file_name is not None else record[4]
        new_display_name = body.display_name if body.display_name is not None else record[5]
        new_status = body.status if body.status is not None else (record[6] or "draft")
        new_published_course_id = (
            body.published_course_id if body.published_course_id is not None else record[7]
        )

        updated = session.execute(
            sa_text("""
                UPDATE course_builder_sessions
                SET title = :title,
                    history = CAST(:history AS jsonb),
                    course_draft = CAST(:draft AS jsonb),
                    source_file_name = :source_file_name,
                    display_name = :display_name,
                    status = :status,
                    published_course_id = :published_course_id,
                    updated_at = now()
                WHERE id = :session_id AND user_id = CAST(:user_id AS uuid)
                RETURNING updated_at
            """),
            {
                "session_id": session_id,
                "user_id": current_user["id"],
                "title": new_title,
                "history": new_history,
                "draft": new_draft,
                "source_file_name": new_source_file_name,
                "display_name": new_display_name,
                "status": new_status,
                "published_course_id": new_published_course_id,
            },
        ).fetchone()
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    created_at = record[3].isoformat() if record[3] else ""
    updated_at = updated[0].isoformat() if updated and updated[0] else ""
    return CourseBuilderSessionOut(
        session_id=session_id, title=new_title,
        display_name=new_display_name, source_file_name=new_source_file_name,
        status=new_status, published_course_id=new_published_course_id,
        created_at=created_at, updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# Course publish & unanswered queries
# ---------------------------------------------------------------------------

@router.get("/courses")
def list_teacher_courses(
    current_user: dict = Depends(_require_teacher),
) -> list[dict]:
    """教員が管理できるコース一覧を返す。"""
    session = _pg_session()
    try:
        # 修正: LEFT JOINによる行増殖を防ぎ、権限の優先順位（owner > editor > viewer）を厳密化
        records = session.execute(
            sa_text("""
                SELECT lc.id, lc.title,
                       COALESCE(lc.is_template, false) AS is_template,
                       COALESCE(lc.is_published, false) AS is_published,
                       lc.created_at, lc.updated_at,
                       CASE
                           WHEN lc.user_id = CAST(:user_id AS uuid) THEN 'owner'
                           WHEN EXISTS (
                               SELECT 1 FROM object_group_permissions cgp
                               JOIN group_members gm ON gm.group_id = cgp.group_id
                               WHERE cgp.object_type = 'course'
                                 AND cgp.object_id = lc.id
                                 AND gm.user_id = CAST(:user_id AS uuid)
                                 AND cgp.permission = 'editor'
                           ) THEN 'editor'
                           ELSE 'viewer'
                       END AS role,
                       lc.data,
                       COALESCE(lc.visibility, 'private') AS visibility,
                       lc.group_id,
                       COALESCE(lc.description, '') AS description
                FROM learning_courses lc
                WHERE lc.user_id = CAST(:user_id AS uuid)
                   OR EXISTS (
                       SELECT 1 FROM object_group_permissions cgp
                       JOIN group_members gm ON gm.group_id = cgp.group_id
                       WHERE cgp.object_type = 'course'
                         AND cgp.object_id = lc.id
                         AND gm.user_id = CAST(:user_id AS uuid)
                         AND cgp.permission IN ('editor', 'viewer')
                   )
                ORDER BY lc.updated_at DESC
            """),
            {"user_id": current_user["id"]},
        ).fetchall()
    finally:
        session.close()

    result = []
    for r in records:
        data = r[7] if len(r) > 7 and isinstance(r[7], dict) else {}
        topics = [t for t in course_topics(data) if isinstance(t, dict)]
        bound_topics = sum(1 for t in topics if str(t.get("atlas_node_id") or "").strip())
        result.append({
            "id": r[0],
            "title": r[1],
            "is_template": bool(r[2]),
            "is_published": bool(r[3]),
            "created_at": r[4].isoformat() if r[4] else "",
            "updated_at": r[5].isoformat() if r[5] else "",
            "role": r[6],  # "owner" | "editor" | "viewer"
            # Atlas operations dashboard projection.  These additive fields do
            # not expose course content; they only let the UI identify courses
            # whose map placement needs attention.
            "atlas_cartridge_id": course_cartridge_id(data),
            "atlas_topic_count": bound_topics,
            "topic_count": len(topics),
            # G1-1/G5-2: 公開状態・開示範囲を管理画面のコース管理テーブルで確認できるようにする。
            "visibility": (r[8] if len(r) > 8 else None) or "private",
            "group_id": str(r[9]) if len(r) > 9 and r[9] else None,
            # 学習マップ編集の「このコースから新しい分野マップを作る」prefill 用 (§2.3)。
            # description 列が空なら course_data 側の description に縮退する。
            "description": (str(r[10]) if len(r) > 10 and r[10] else "")
            or str(data.get("description") or ""),
        })
    return result


@router.get("/courses/{course_id}/draft-format")
def get_course_as_draft(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """登録済みコースのデータを course_draft 形式に変換して返す。

    Course Builder にインポートするためのアダプタエンドポイント。
    所有者に加え、editor 権限グループのメンバーもアクセスできる。
    """
    if not user_can_edit_course(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")

    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT data, title FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()

    if not record:
        raise HTTPException(status_code=404, detail="Course not found")

    data = record[0] if isinstance(record[0], dict) else (
        json.loads(record[0]) if record[0] else {}
    )
    course_title = record[1] or _course_title(data)

    # --- Convert registered course data → course_draft format ---
    topics = course_topics(data)
    chapters_raw = course_chapters(data)

    # Group topics by chapter_index
    chapter_topics: dict[int, list] = {}
    for t in topics:
        ci = t.get("chapter_index", 0)
        if ci not in chapter_topics:
            chapter_topics[ci] = []
        prereqs = []
        for p in t.get("prerequisites", []):
            name = p.get("name", "") if isinstance(p, dict) else str(p)
            if name:
                prereqs.append(name)
        chapter_topics[ci].append({
            "title": t.get("title", ""),
            "prerequisites": prereqs,
        })

    # Build chapters with topics
    chapters = []
    for ci, ch in enumerate(chapters_raw):
        chapters.append({
            "title": ch.get("title", ""),
            "topics": chapter_topics.get(ci, []),
        })

    # Build concepts
    concepts = []
    for c in data.get("concepts", []):
        concepts.append({
            "name": c.get("name", ""),
            "children": c.get("children", []),
        })

    # Build sources
    sources = []
    for s in course_sources(data):
        sources.append({
            "title": s.get("title", ""),
            "subtitle": s.get("subtitle", ""),
            "license": s.get("license", ""),
            "used_section": s.get("used_section", ""),
            "material_id": s.get("material_id", ""),
        })

    draft = {
        "title": course_title,
        "domain": data.get("domain", ""),
        "target_audience": data.get("target_audience", ""),
        "goal": data.get("goal", ""),
        "prerequisites": [],
        "chapters": chapters,
        "concepts": concepts,
        "sources": sources,
    }

    return {
        "course_id": course_id,
        "course_title": course_title,
        "course_draft": draft,
    }


# ---------------------------------------------------------------------------
# Course × Group Permissions (Issue #125)
# ---------------------------------------------------------------------------


@router.get(
    "/courses/{course_id}/groups",
    response_model=list[CourseGroupPermissionOut],
)
def list_course_group_permissions(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> list[CourseGroupPermissionOut]:
    """コースに紐づくグループ権限マッピングの一覧を返す。

    所有者、editor/viewer 権限グループのメンバーいずれもが現在の共有設定を
    参照できる（表示用）。変更は所有者のみ。
    """
    if not user_can_view_course(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")
    rows = get_course_group_permissions(course_id)
    return [CourseGroupPermissionOut(**r) for r in rows]


@router.post(
    "/courses/{course_id}/groups",
    response_model=CourseGroupPermissionOut,
    status_code=201,
)
def upsert_course_group_permission(
    course_id: str,
    body: CourseGroupPermissionUpsertRequest,
    current_user: dict = Depends(_require_teacher),
) -> CourseGroupPermissionOut:
    """コースにグループ権限を付与する（既存なら権限を更新）。

    共有設定の変更はコースの所有者のみ可能。
    """
    if body.permission not in ("viewer", "editor"):
        raise HTTPException(
            status_code=400, detail="permission must be 'viewer' or 'editor'",
        )
    if not user_owns_course(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")

    session = _pg_session()
    try:
        group_row = session.execute(
            sa_text("SELECT name FROM groups WHERE id = CAST(:gid AS uuid) LIMIT 1"),
            {"gid": body.group_id},
        ).fetchone()
        if not group_row:
            raise HTTPException(status_code=404, detail="Group not found")

        row = session.execute(
            sa_text("""
                INSERT INTO object_group_permissions
                    (object_type, object_id, group_id, permission)
                VALUES ('course', :course_id, CAST(:gid AS uuid), :permission)
                ON CONFLICT (object_type, object_id, group_id) DO UPDATE
                SET permission = EXCLUDED.permission,
                    updated_at = now()
                RETURNING object_id, group_id, permission, created_at, updated_at
            """),
            {
                "course_id": course_id,
                "gid": body.group_id,
                "permission": body.permission,
            },
        ).fetchone()
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info(
        "Course %s group permission set: group=%s permission=%s by user=%s",
        course_id, body.group_id, body.permission, current_user["id"],
    )
    return CourseGroupPermissionOut(
        course_id=row[0],
        group_id=str(row[1]),
        group_name=group_row[0] or "",
        permission=row[2],
        created_at=row[3].isoformat() if row[3] else "",
        updated_at=row[4].isoformat() if row[4] else "",
    )


@router.delete("/courses/{course_id}/groups/{group_id}")
def delete_course_group_permission(
    course_id: str,
    group_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースからグループ権限マッピングを削除する。

    共有設定の変更はコースの所有者のみ可能。
    """
    if not user_owns_course(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")

    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                DELETE FROM object_group_permissions
                WHERE object_type = 'course' AND object_id = :course_id
                  AND group_id = CAST(:gid AS uuid)
                RETURNING object_id
            """),
            {"course_id": course_id, "gid": group_id},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if not result:
        raise HTTPException(status_code=404, detail="Permission mapping not found")
    logger.info(
        "Course %s group permission removed: group=%s by user=%s",
        course_id, group_id, current_user["id"],
    )
    return {"course_id": course_id, "group_id": group_id, "deleted": True}


# ---------------------------------------------------------------------------
# ドキュメント（教材・パイプライン成果）× グループ 共有（migration 044、旧 035）
# ---------------------------------------------------------------------------
# コースを作らずに解析成果（theory_components / theory_claims / graphs / analysis_runs、
# すべて document_id 由来）を指定グループへ共有する。object_group_permissions
# （object_type='document'）を使う。


@router.get(
    "/documents/{document_id}/groups",
    response_model=list[DocumentGroupPermissionOut],
)
def list_document_group_permissions(
    document_id: str,
    current_user: dict = Depends(_require_teacher),
) -> list[DocumentGroupPermissionOut]:
    """ドキュメントに紐づくグループ共有設定の一覧を返す。

    閲覧可能な者（所有者・共有先グループメンバー）は現在の設定を参照できる。変更は所有者のみ。
    """
    if not user_can_view_document(current_user["id"], document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    rows = get_document_group_permissions(document_id)
    return [DocumentGroupPermissionOut(**r) for r in rows]


@router.post(
    "/documents/{document_id}/groups",
    response_model=DocumentGroupPermissionOut,
    status_code=201,
)
def upsert_document_group_permission(
    document_id: str,
    body: DocumentGroupPermissionUpsertRequest,
    current_user: dict = Depends(_require_teacher),
) -> DocumentGroupPermissionOut:
    """ドキュメントの解析成果をグループへ共有する（既存なら権限を更新）。共有設定の変更は所有者のみ。"""
    if body.permission not in ("viewer", "editor"):
        raise HTTPException(
            status_code=400, detail="permission must be 'viewer' or 'editor'",
        )
    doc = _resolve_document(document_id)
    if not doc or not user_owns_document(current_user["id"], document_id):
        raise HTTPException(status_code=404, detail="Document not found")

    session = _pg_session()
    try:
        group_row = session.execute(
            sa_text("SELECT name FROM groups WHERE id = CAST(:gid AS uuid) LIMIT 1"),
            {"gid": body.group_id},
        ).fetchone()
        if not group_row:
            raise HTTPException(status_code=404, detail="Group not found")

        row = session.execute(
            sa_text("""
                INSERT INTO object_group_permissions
                    (object_type, object_id, group_id, permission)
                VALUES ('document', CAST(:did AS uuid)::text, CAST(:gid AS uuid), :permission)
                ON CONFLICT (object_type, object_id, group_id) DO UPDATE
                SET permission = EXCLUDED.permission,
                    updated_at = now()
                RETURNING object_id, group_id, permission, created_at, updated_at
            """),
            {"did": doc["id"], "gid": body.group_id, "permission": body.permission},
        ).fetchone()
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    # 監査: 共有付与を theory_review_events に記録（entity_type='document_share'）
    record_review_event(
        AUDIT_ENTITY_DOCUMENT_SHARE, doc["id"], "", body.permission, current_user["id"],
        {"group_id": body.group_id, "action": "grant"},
    )
    logger.info(
        "Document %s group permission set: group=%s permission=%s by user=%s",
        doc["id"], body.group_id, body.permission, current_user["id"],
    )
    return DocumentGroupPermissionOut(
        document_id=str(row[0]),
        group_id=str(row[1]),
        group_name=group_row[0] or "",
        permission=row[2],
        created_at=row[3].isoformat() if row[3] else "",
        updated_at=row[4].isoformat() if row[4] else "",
    )


@router.delete("/documents/{document_id}/groups/{group_id}")
def delete_document_group_permission(
    document_id: str,
    group_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """ドキュメントからグループ共有を解除する。共有設定の変更は所有者のみ。"""
    doc = _resolve_document(document_id)
    if not doc or not user_owns_document(current_user["id"], document_id):
        raise HTTPException(status_code=404, detail="Document not found")

    session = _pg_session()
    try:
        result = session.execute(
            sa_text("""
                DELETE FROM object_group_permissions
                WHERE object_type = 'document' AND object_id = CAST(:did AS uuid)::text
                  AND group_id = CAST(:gid AS uuid)
                RETURNING object_id
            """),
            {"did": doc["id"], "gid": group_id},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if not result:
        raise HTTPException(status_code=404, detail="Permission mapping not found")
    record_review_event(
        AUDIT_ENTITY_DOCUMENT_SHARE, doc["id"], "shared", "removed", current_user["id"],
        {"group_id": group_id, "action": "revoke"},
    )
    logger.info(
        "Document %s group permission removed: group=%s by user=%s",
        doc["id"], group_id, current_user["id"],
    )
    return {"document_id": doc["id"], "group_id": group_id, "deleted": True}


# ---------------------------------------------------------------------------
# 図画像（画像パイプライン §4-3 / §7、migration 041）
# ---------------------------------------------------------------------------
# _ensure_document_viewable（routes/theory_components.py）を必ず通す（原則7:
# 抽出画像は元 document の権限を継承する）。document_id は UUID / material_id の
# どちらでも解決できるが、document_figures.document_id は常に documents.id
# （UUID 文字列）で保存されている（figure_image_extraction が orchestrator の
# document_id=doc_id をそのまま使うため）ので、chunks から解決した canonical な
# document_id で照会する。


def _canonical_document_id(chunks: list[dict], fallback: str) -> str:
    for chunk in chunks:
        did = chunk.get("document_id")
        if did:
            return did
    return fallback


@router.get("/documents/{document_id}/figures")
def list_document_figures(
    document_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """図画像一覧（caption・抽出方式・装置候補つき）を返す。"""
    chunks = _ensure_document_viewable(document_id, current_user)
    canonical_document_id = _canonical_document_id(chunks, document_id)

    rows = load_document_figures(canonical_document_id)

    # 最新 run の apparatus_semantics artifact から figure_id 単位の候補を拾う
    # （無ければ空リスト。apparatus_semantics は常に review_required 系の
    # candidate であり、ここでは表示用に必要なフィールドだけを抜粋する）。
    apparatus_by_figure: dict[str, list[dict]] = {}
    try:
        latest_run = get_latest_analysis_run(document_id=canonical_document_id)
        stage_outputs = (latest_run or {}).get("stage_outputs") or {}
        artifacts = stage_outputs.get("_artifacts") or {}
        apparatus_artifact = artifacts.get("apparatus_semantics") or {}
        for record in apparatus_artifact.get("apparatus_records") or []:
            fig_id = str(record.get("figure_id") or "")
            if not fig_id:
                continue
            apparatus_by_figure.setdefault(fig_id, []).append({
                "apparatus_name_candidate": record.get("apparatus_name_candidate", ""),
                "match_status": record.get("match_status", "unknown"),
                "parts": [
                    {
                        "name": p.get("name", ""),
                        "role": p.get("role", ""),
                        # Phase 2 拡張（label_ref/expanded_name/bbox/evidence_quote/reason/confidence）。
                        # 旧 artifact にはキーが無いため .get() でデフォルトを与える（情報を落とさない）。
                        "label_ref": p.get("label_ref"),
                        "expanded_name": p.get("expanded_name", ""),
                        "bbox": p.get("bbox"),
                        "evidence_quote": p.get("evidence_quote", ""),
                        "reason": p.get("reason", ""),
                        "confidence": p.get("confidence", 0.0),
                    }
                    for p in (record.get("parts") or [])
                    if isinstance(p, dict)
                ],
                "confidence": record.get("confidence", 0.0),
                "review_status": record.get("review_status", "review_required"),
                "matched_library_entry_id": record.get("matched_library_entry_id"),
            })
    except Exception:
        logger.warning(
            "list_document_figures: failed to load apparatus candidates for document=%s",
            canonical_document_id, exc_info=True,
        )

    figures = []
    for row in rows:
        fig_id = str(row.get("id") or "")
        figures.append({
            "id": fig_id,
            "figure_key": row.get("figure_key"),
            "figure_label": row.get("figure_label"),
            "page": row.get("page"),
            "caption_text": row.get("caption_text"),
            "extraction_method": row.get("extraction_method"),
            "region_confidence": row.get("region_confidence"),
            "status": row.get("status"),
            "image_url": f"/api/admin/documents/{document_id}/figures/{fig_id}/image",
            "bbox": row.get("bbox"),
            "inner_labels": row.get("inner_labels") or [],
            "apparatus_candidates": apparatus_by_figure.get(fig_id, []),
        })
    return {"figures": figures}


@router.get("/documents/{document_id}/figures/{figure_id}/image")
def get_document_figure_image(
    document_id: str,
    figure_id: str,
    current_user: dict = Depends(_require_teacher),
) -> Response:
    """図画像そのものを配信する（MinIO bucket figure-images）。"""
    chunks = _ensure_document_viewable(document_id, current_user)
    canonical_document_id = _canonical_document_id(chunks, document_id)

    rows = load_document_figures(canonical_document_id)
    row = next((r for r in rows if str(r.get("id")) == figure_id), None)
    if not row or not row.get("minio_key"):
        raise HTTPException(status_code=404, detail="Figure not found")

    try:
        image_bytes = get_storage_client().get_object("figure-images", row["minio_key"])
    except Exception:
        logger.warning(
            "get_document_figure_image: MinIO fetch failed document=%s figure=%s",
            canonical_document_id, figure_id, exc_info=True,
        )
        raise HTTPException(status_code=404, detail="Figure image not found")

    return Response(content=image_bytes, media_type="image/png")


@router.delete("/courses/{course_id}")
def delete_course(
    course_id: str,
    body: DeleteConfirmRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コースを削除する。

    confirm_name がコースタイトルと一致しない場合は 400 を返す。
    所有者、または editor 権限グループのメンバーのみ削除可能。
    """
    if not user_can_edit_course(current_user["id"], course_id):
        raise HTTPException(status_code=404, detail="Course not found")

    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT id, title FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()

        if not record:
            raise HTTPException(status_code=404, detail="Course not found")

        course_title = record[1] or ""
        if body.confirm_name != course_title:
            raise HTTPException(
                status_code=400,
                detail="確認用の名前が一致しません。正確なコース名を入力してください。",
            )

        # V層（migration 037）: 削除でグループ権限が消える前に通知宛先を集めておく
        course_recipients = _versioning_collect_recipients("course", course_id)

        # 関連する学習チャット履歴を削除
        session.execute(
            sa_text("DELETE FROM learning_chat_history WHERE course_id = :cid"),
            {"cid": course_id},
        )
        # object_group_permissions は course_id への FK が無いポリモーフィック
        # テーブルなので明示削除する（孤児防止。migration 044。旧テーブルは
        # learning_courses への FK CASCADE で自動的に消えていた）。
        session.execute(
            sa_text(
                "DELETE FROM object_group_permissions "
                "WHERE object_type = 'course' AND object_id = :course_id"
            ),
            {"course_id": course_id},
        )
        # コース削除
        session.execute(
            sa_text("DELETE FROM learning_courses WHERE id = :course_id"),
            {"course_id": course_id},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info("Course %s (%s) deleted by user=%s", course_id, course_title, current_user["id"])
    # V層（migration 037）: コースの共有版状態を掃除し購読者へ通知する（orphan gap 解消）
    _versioning_teardown_after_delete(
        [("course", course_id)], primary_recipients=course_recipients, actor=current_user["id"],
    )
    return {"course_id": course_id, "deleted": True}


@router.get("/courses/{course_id}/unanswered-queries")
def list_unanswered_queries(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> list[dict]:
    """コースに紐づくつまづきデータ（RAG未回答クエリ）を返す。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT uql.id, uql.topic_id, uql.question, uql.asked_at,
                       u.display_name AS student_name
                FROM unanswered_query_logs uql
                JOIN users u ON uql.user_id = u.id
                WHERE uql.course_id = :course_id
                ORDER BY uql.asked_at DESC
                LIMIT 200
            """),
            {"course_id": course_id},
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "id": r[0],
            "topic_id": r[1],
            "question": r[2],
            "asked_at": r[3].isoformat() if r[3] else "",
            "student_name": r[4] or "",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# System Statistics (Issue #144)
# ---------------------------------------------------------------------------


@router.get("/system/materials-stats")
def get_materials_stats(
    current_user: dict = Depends(_require_system_admin),
) -> list[dict]:
    """全コースのパイプライン進捗・利用統計を返す（SYSTEM_ADMIN 専用）。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                WITH CourseSources AS (
                    SELECT
                        lc.id::text AS course_id,
                        (src->>'material_id') AS material_id
                    FROM learning_courses lc,
                    LATERAL jsonb_array_elements(
                        CASE WHEN jsonb_typeof(lc.data->'sources') = 'array'
                             THEN lc.data->'sources'
                             ELSE '[]'::jsonb
                        END
                    ) AS src
                    WHERE src->>'material_id' IS NOT NULL
                ),
                ChunkStats AS (
                    SELECT
                        cs.course_id,
                        COUNT(DISTINCT c.id) AS total_chunks,
                        COUNT(DISTINCT CASE
                            WHEN c.smiles_dsl IS NOT NULL AND c.smiles_dsl != '' THEN c.id
                        END) AS structure_chunks,
                        COUNT(DISTINCT CASE
                            WHEN c.spoken_text IS NOT NULL AND c.spoken_text != '' THEN c.id
                        END) AS script_chunks,
                        COUNT(DISTINCT a.chunk_id) AS audio_chunks
                    FROM CourseSources cs
                    JOIN chunks c ON c.material_id = cs.material_id
                    LEFT JOIN lecture_audio_cache a ON a.chunk_id = c.id
                    WHERE c.text IS NOT NULL AND c.text != ''
                    GROUP BY cs.course_id
                ),
                SectionStats AS (
                    SELECT
                        cs.course_id,
                        COUNT(DISTINCT (
                            COALESCE(c.document_id::text, c.material_id, '') || ':' ||
                            CASE
                                WHEN c.page_start IS NOT NULL THEN 'page_' || c.page_start::text
                                ELSE 'section_' || (floor(COALESCE(c.chunk_index, 0) / 4.0)::int + 1)::text
                            END
                        )) AS total_sections
                    FROM CourseSources cs
                    JOIN chunks c ON c.material_id = cs.material_id
                    WHERE c.text IS NOT NULL AND c.text != ''
                    GROUP BY cs.course_id
                ),
                ClaimStats AS (
                    SELECT
                        cs.course_id,
                        COUNT(DISTINCT tc.chunk_id) AS claim_chunks
                    FROM CourseSources cs
                    JOIN chunks c ON c.material_id = cs.material_id
                    JOIN theory_claims tc ON tc.chunk_id = c.id
                    GROUP BY cs.course_id
                ),
                ComponentStats AS (
                    SELECT
                        course_id,
                        COUNT(DISTINCT source_scope->>'section_id') AS component_sections
                    FROM theory_components
                    WHERE source_scope->>'level' = 'section'
                    GROUP BY course_id
                ),
                GraphStats AS (
                    SELECT
                        course_id,
                        COUNT(DISTINCT document_id) AS graph_documents
                    FROM theory_component_graphs
                    GROUP BY course_id
                ),
                DocumentStats AS (
                    SELECT
                        cs.course_id,
                        COUNT(DISTINCT COALESCE(c.document_id::text, c.material_id)) AS total_documents
                    FROM CourseSources cs
                    JOIN chunks c ON c.material_id = cs.material_id
                    WHERE c.text IS NOT NULL AND c.text != ''
                    GROUP BY cs.course_id
                ),
                EnrolledStats AS (
                    SELECT
                        ls.course_id,
                        COUNT(DISTINCT ls.user_id) AS enrolled_students
                    FROM learning_states ls
                    GROUP BY ls.course_id
                ),
                ChatStats AS (
                    SELECT
                        lch.course_id,
                        COALESCE(SUM(jsonb_array_length(lch.history)), 0) AS chat_count
                    FROM learning_chat_history lch
                    GROUP BY lch.course_id
                ),
                ActiveTasks AS (
                    SELECT DISTINCT ON (result_data->>'course_id')
                        result_data->>'course_id' AS course_id,
                        task_type
                    FROM background_tasks
                    WHERE status IN ('pending', 'processing')
                      AND result_data IS NOT NULL
                      AND result_data->>'course_id' IS NOT NULL
                    ORDER BY result_data->>'course_id', created_at DESC
                )
                SELECT
                    lc.id::text AS course_id,
                    lc.title,
                    u.display_name AS uploaded_by,
                    lc.created_at,
                    COALESCE(cs.total_chunks, 0) AS chunk_count,
                    CASE
                        WHEN COALESCE(cs.total_chunks, 0) = 0 THEN 0.0
                        ELSE ROUND(COALESCE(cs.structure_chunks, 0)::numeric / cs.total_chunks * 100, 1)
                    END AS structure_progress,
                    CASE
                        WHEN COALESCE(cs.total_chunks, 0) = 0 THEN 0.0
                        ELSE ROUND(COALESCE(claims.claim_chunks, 0)::numeric / cs.total_chunks * 100, 1)
                    END AS claim_progress,
                    CASE
                        WHEN COALESCE(sections.total_sections, 0) = 0 THEN 0.0
                        ELSE ROUND(COALESCE(components.component_sections, 0)::numeric / sections.total_sections * 100, 1)
                    END AS component_progress,
                    CASE
                        WHEN COALESCE(docs.total_documents, 0) = 0 THEN 0.0
                        ELSE ROUND(COALESCE(graphs.graph_documents, 0)::numeric / docs.total_documents * 100, 1)
                    END AS graph_progress,
                    CASE
                        WHEN COALESCE(cs.total_chunks, 0) = 0 THEN 0.0
                        ELSE ROUND(cs.script_chunks::numeric / cs.total_chunks * 100, 1)
                    END AS script_progress,
                    CASE
                        WHEN COALESCE(cs.total_chunks, 0) = 0 THEN 0.0
                        ELSE ROUND(COALESCE(cs.audio_chunks, 0)::numeric / cs.total_chunks * 100, 1)
                    END AS audio_progress,
                    COALESCE(es.enrolled_students, 0) AS enrolled_students,
                    COALESCE(chats.chat_count, 0) AS chat_count,
                    active.task_type AS active_task_type
                FROM learning_courses lc
                LEFT JOIN users u ON u.id = lc.user_id
                LEFT JOIN ChunkStats cs ON cs.course_id = lc.id::text
                LEFT JOIN SectionStats sections ON sections.course_id = lc.id::text
                LEFT JOIN ClaimStats claims ON claims.course_id = lc.id::text
                LEFT JOIN ComponentStats components ON components.course_id = lc.id::text
                LEFT JOIN GraphStats graphs ON graphs.course_id = lc.id::text
                LEFT JOIN DocumentStats docs ON docs.course_id = lc.id::text
                LEFT JOIN EnrolledStats es ON es.course_id = lc.id
                LEFT JOIN ChatStats chats ON chats.course_id = lc.id
                LEFT JOIN ActiveTasks active ON active.course_id = lc.id::text
                ORDER BY lc.created_at DESC
            """),
        ).fetchall()
    finally:
        session.close()

    return [
        {
            "course_id": r[0],
            "title": r[1] or "",
            "uploaded_by": r[2] or "",
            "created_at": r[3].isoformat() if r[3] else "",
            "chunk_count": int(r[4]) if r[4] else 0,
            "structure_progress": float(r[5]) if r[5] is not None else 0.0,
            "claim_progress": float(r[6]) if r[6] is not None else 0.0,
            "component_progress": float(r[7]) if r[7] is not None else 0.0,
            "graph_progress": float(r[8]) if r[8] is not None else 0.0,
            "script_progress": float(r[9]) if r[9] is not None else 0.0,
            "audio_progress": float(r[10]) if r[10] is not None else 0.0,
            "enrolled_students": int(r[11]) if r[11] else 0,
            "chat_count": int(r[12]) if r[12] else 0,
            "active_task_type": r[13] or "",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------


@router.post("/users/student", response_model=UserOut, status_code=201)
def create_student(
    body: CreateUserRequest,
    current_user: dict = Depends(_require_teacher),
) -> UserOut:
    """教員が学生アカウントを作成する。"""
    session = _pg_session()
    try:
        existing = session.execute(
            sa_text("SELECT id FROM users WHERE display_name = :username LIMIT 1"),
            {"username": body.username},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already taken")

        user_id = uuid.uuid4()
        hashed_pw = _hash_password(body.password)
        session.execute(
            sa_text("""
                INSERT INTO users (id, email, display_name, role, password_hash)
                VALUES (:id, :email, :username, 'learner', :pw)
            """),
            {"id": user_id, "email": body.email, "username": body.username, "pw": hashed_pw},
        )
        session.commit()
    finally:
        session.close()

    logger.info("Teacher '%s' created student '%s' (id=%s)", current_user["username"], body.username, user_id)
    return UserOut(id=str(user_id), username=body.username, email=body.email, role=ROLE_STUDENT)


@router.post("/users/teacher", response_model=UserOut, status_code=201)
def create_teacher(
    body: CreateUserRequest,
    current_user: dict = Depends(_require_system_admin),
) -> UserOut:
    """管理者が教員アカウントを作成する。"""
    session = _pg_session()
    try:
        existing = session.execute(
            sa_text("SELECT id FROM users WHERE display_name = :username LIMIT 1"),
            {"username": body.username},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already taken")

        user_id = uuid.uuid4()
        hashed_pw = _hash_password(body.password)
        session.execute(
            sa_text("""
                INSERT INTO users (id, email, display_name, role, password_hash)
                VALUES (:id, :email, :username, 'instructor', :pw)
            """),
            {"id": user_id, "email": body.email, "username": body.username, "pw": hashed_pw},
        )
        session.commit()
    finally:
        session.close()

    logger.info("Admin '%s' created teacher '%s' (id=%s)", current_user["username"], body.username, user_id)
    return UserOut(id=str(user_id), username=body.username, email=body.email, role=ROLE_TEACHER)


# ---------------------------------------------------------------------------
# Schema Evolution (Issue #36)
# ---------------------------------------------------------------------------


@router.get("/schema/types", response_model=list[SchemaTypeOut])
def list_ontology_types(
    current_user: dict = Depends(_require_teacher),
) -> list[SchemaTypeOut]:
    """登録済みOntologyType一覧を返す。"""
    types = get_ontology_types()
    return [SchemaTypeOut(**t) for t in types]


@router.post("/schema/types", response_model=SchemaTypeOut, status_code=201)
def create_ontology_type(
    body: SchemaTypeCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> SchemaTypeOut:
    """新しいOntologyTypeを追加する。"""
    add_ontology_type(body.id, body.label, body.description)
    logger.info("OntologyType '%s' added by user=%s", body.id, current_user["id"])
    return SchemaTypeOut(id=body.id, label=body.label, description=body.description, is_builtin=False)


@router.get("/schema/predicates", response_model=list[SchemaTypeOut])
def list_predicates(
    current_user: dict = Depends(_require_teacher),
) -> list[SchemaTypeOut]:
    """登録済みCorePredicate一覧を返す。"""
    preds = get_predicates()
    return [SchemaTypeOut(**p) for p in preds]


@router.post("/schema/predicates", response_model=SchemaTypeOut, status_code=201)
def create_predicate(
    body: SchemaTypeCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> SchemaTypeOut:
    """新しいCorePredicateを追加する。"""
    add_predicate(body.id, body.label, body.description)
    logger.info("Predicate '%s' added by user=%s", body.id, current_user["id"])
    return SchemaTypeOut(id=body.id, label=body.label, description=body.description, is_builtin=False)


@router.get("/schema-proposals", response_model=list[SchemaProposalOut])
def list_schema_proposals(
    status: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> list[SchemaProposalOut]:
    """スキーマ拡張提案一覧を返す。"""
    proposals = get_proposals(status=status)
    return [SchemaProposalOut(**p) for p in proposals]


@router.post("/schema-proposals/analyze", response_model=SchemaProposalOut | dict)
def trigger_schema_analysis(
    current_user: dict = Depends(_require_teacher),
) -> SchemaProposalOut | dict:
    """未回答クエリを分析してスキーマ拡張提案を生成する。"""
    result = analyze_unanswered_queries()
    if result is None:
        return {"message": "分析の結果、スキーマ拡張の提案はありません。未回答クエリが不足しているか、現在のスキーマで十分カバーされています。"}
    return SchemaProposalOut(**result)


@router.put("/schema-proposals/{proposal_id}/approve")
def approve_schema_proposal(
    proposal_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """スキーマ拡張提案を承認し、新しいType/Predicateを登録する。"""
    success = approve_proposal(proposal_id, current_user["id"])
    if not success:
        raise HTTPException(status_code=404, detail="Proposal not found or already reviewed")

    # 再抽出ジョブをキューに追加
    job = enqueue_reextraction(proposal_id)
    logger.info(
        "Schema proposal %s approved by user=%s, reextraction job %s enqueued",
        proposal_id, current_user["id"], job["job_id"],
    )
    return {
        "proposal_id": proposal_id,
        "status": "approved",
        "reextraction_job": job,
    }


@router.put("/schema-proposals/{proposal_id}/reject")
def reject_schema_proposal(
    proposal_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """スキーマ拡張提案を却下する。"""
    success = reject_proposal(proposal_id, current_user["id"])
    if not success:
        raise HTTPException(status_code=404, detail="Proposal not found or already reviewed")
    return {"proposal_id": proposal_id, "status": "rejected"}


@router.get("/reextraction-jobs", response_model=list[ReextractionJobOut])
def list_reextraction_jobs(
    current_user: dict = Depends(_require_teacher),
) -> list[ReextractionJobOut]:
    """再抽出ジョブ一覧を返す。"""
    jobs = get_reextraction_jobs()
    return [ReextractionJobOut(**j) for j in jobs]


# ---------------------------------------------------------------------------
# Shadow Testing / Simulation (Issue #45)
# ---------------------------------------------------------------------------


@router.post(
    "/schema-proposals/{proposal_id}/simulate",
    response_model=SimulationResponse,
)
def simulate_schema_proposal(
    proposal_id: str,
    current_user: dict = Depends(_require_teacher),
) -> SimulationResponse:
    """スキーマ提案のShadow Testingシミュレーションを実行する。

    Target/Similar/Control の3層のドキュメントに対して新スキーマを
    テスト適用し、差分（Diff）を返す。
    """
    from core.simulator import run_simulation

    result = run_simulation(proposal_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Proposal not found",
        )
    return SimulationResponse(**result)


@router.put("/schema-proposals/{proposal_id}/approve-with-scope")
def approve_schema_proposal_with_scope(
    proposal_id: str,
    body: ApproveWithScopeRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """スキーマ提案をスコープ付きで承認する。

    scope="full": システム全体に適用
    scope="canary": 指定コースのみに適用（カナリアリリース）
    """
    from core.simulator import approve_with_scope

    result = approve_with_scope(
        proposal_id=proposal_id,
        reviewer_id=current_user["id"],
        scope=body.scope,
        course_ids=body.course_ids,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Proposal not found or already reviewed",
        )
    logger.info(
        "Schema proposal %s approved (scope=%s) by user=%s",
        proposal_id, body.scope, current_user["id"],
    )
    return result


# ---------------------------------------------------------------------------
# 学習者体験レイヤー(B層) — 教員向け関心集約ダッシュボード (Stage 4)
# ---------------------------------------------------------------------------
@router.get("/interest-dashboard")
def get_interest_dashboard(
    course_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教員向け InterestDashboard（集団集計を既定・L4）。

    interest_traces を集団集計し、件数・比率・関与人数のみを返す（個人特定情報なし）。
    course_id 未指定なら空集計を返す（フロントでコースを選択する）。
    """
    from services import aggregate_interest_dashboard, _fetch_course_data_row

    if not course_id:
        return {"course_id": None, "cohort_size": 0, "hotspots": [],
                "unfinished_summary": {"open_questions": 0, "repeated_detours": 0, "recurring_misconceptions": 0}}

    title_map: dict = {}
    try:
        data = _fetch_course_data_row(course_id) or {}
        for t in course_topics(data):
            if isinstance(t, dict) and t.get("id"):
                title_map[t["id"]] = t.get("title") or t["id"]
    except Exception:
        title_map = {}

    return aggregate_interest_dashboard(course_id, title_map)


# ---------------------------------------------------------------------------
# 知識ネットワークビジョン Phase B — 学習者重ね合わせの橋候補（教員向け）
# ---------------------------------------------------------------------------
@router.get("/courses/{course_id}/bridge-insights")
def get_bridge_insights(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教員向け橋候補インサイト（ビジョン §3 修正①・§4 KN-4・§7 Phase B）。

    学習者が tension の connect 操作で自分の引っかかりを公共構造
    （theory_component / TheoryOperationGraph の edge）へ自分で繋いだ「橋」を、
    コース単位で k-匿名集約（k=3・人数はレンジ表示のみ。正本は core/privacy.py）した
    橋候補として返す。ゲート・集約の流儀は interest-dashboard（B層教員向け集約）と同一。

    - 個別の学習者・個別の痕跡行は一切返さない（PN-1/P3・評価利用禁止）
    - 教員への候補提示のみで、ドメイン知識候補への自動昇格経路は作らない（KN-4）
    - 「繋がりを見失う」側（迷いの集約）は v1 見送り（橋のみ）
    """
    from core.personal_graph.bridges import aggregate_bridge_candidates

    bridges = aggregate_bridge_candidates(course_id)
    return {
        "course_id": course_id,
        "bridges": bridges,
        "note": "学習者個人は特定できません（k-匿名集約・人数はレンジ表示のみ）。評価利用は禁止です。",
    }


# ---------------------------------------------------------------------------
# Tier 3-17c: 従来ここで13個の子ルーター（lecture_studio / theory_components /
# cartridges / revisions / atlas 系 / doubt / admin_assistant / reconstruction /
# versioning / status / notifications）を import + include_router していたが、
# 「router(prefix=/api/admin) に子ルーターをネストする」二段構造をやめ、
# api/main.py から `app.include_router(<router>, prefix="/api/admin")` で
# 直接マウントするフラット構造に統一した（URL・認可・レスポンスは不変）。
# ---------------------------------------------------------------------------
