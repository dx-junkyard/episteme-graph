"""Release（不変スナップショット）の発行・スナップショット・一覧・状態。

- course の Release は {title, description, data} を丸ごと保存する。
- document の Release は成果物を複製せず、既に不変な analysis_run_id と表示用 manifest だけを
  ピンする（migration 019 の不変性を再利用）。
- publish は shared_version_state を FOR UPDATE してから version_no を採番し、UNIQUE 競合を防ぐ。
  既存の消費者ピンは動かさない（発行しても消費側は同意するまで旧版のまま）。
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text as sa_text

from core.postgres import get_session

from . import schema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# スナップショット構築
# ---------------------------------------------------------------------------

def build_course_snapshot(course_id: str) -> dict:
    """コースの現在の working copy を丸ごとスナップショットする。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT title, COALESCE(description, ''), data
                FROM learning_courses
                WHERE id = :course_id
                LIMIT 1
            """),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise schema.VersioningError(f"course not found: {course_id}")
    data = row[2] if isinstance(row[2], dict) else json.loads(row[2] or "{}")
    return {"title": row[0] or "", "description": row[1] or "", "data": data}


def build_document_snapshot(document_id: str) -> dict:
    """ドキュメント成果物を analysis_run_id + 表示用 manifest でピンする。

    document_id は呼び出し側で documents.id::text に正規化済みであること。
    大きな成果物本体は複製しない（run.stage_outputs が不変なので run をピンすれば足りる）。
    """
    session = get_session()
    try:
        doc = session.execute(
            sa_text("""
                SELECT source_path, active_analysis_run_id::text
                FROM documents
                WHERE id = CAST(:document_id AS uuid)
                LIMIT 1
            """),
            {"document_id": document_id},
        ).fetchone()
        if not doc:
            raise schema.VersioningError(f"document not found: {document_id}")
        source_path = doc[0] or ""
        run_id = doc[1]

        cartridge_id = ""
        if run_id:
            run = session.execute(
                sa_text("SELECT cartridge_id FROM document_analysis_runs WHERE id = CAST(:rid AS uuid)"),
                {"rid": run_id},
            ).fetchone()
            cartridge_id = (run[0] if run else "") or ""

        # manifest（表示用の件数。best-effort。document_id は UUID / material_id 両形を許容）
        manifest = _document_manifest(session, document_id, source_path)
    finally:
        session.close()
    return {
        "analysis_run_id": run_id or "",
        "cartridge_id": cartridge_id,
        "manifest": manifest,
        "source_path": source_path,
    }


def _document_manifest(session, document_id: str, source_path: str) -> dict:
    """成果物の件数を数える（表示用。失敗しても空で返す）。"""
    try:
        params = {"a": document_id, "b": source_path or document_id}
        n_claims = session.execute(
            sa_text("SELECT count(*) FROM theory_claims WHERE document_id IN (:a, :b)"),
            params,
        ).scalar() or 0
        n_components = session.execute(
            sa_text("SELECT count(*) FROM theory_components WHERE document_id IN (:a, :b)"),
            params,
        ).scalar() or 0
        has_graph = bool(session.execute(
            sa_text("SELECT 1 FROM theory_component_graphs WHERE document_id IN (:a, :b) LIMIT 1"),
            params,
        ).fetchone())
        return {"n_claims": int(n_claims), "n_components": int(n_components), "has_graph": has_graph}
    except Exception:  # noqa: BLE001 — manifest は表示専用
        logger.debug("document manifest count skipped", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# 発行
# ---------------------------------------------------------------------------

def publish_release(*, object_type: str, object_id: str, note: str, published_by: str | None) -> dict:
    """新しい Release を発行して active にする（既存ピンは不動）。

    raises:
        PurgedError / PendingDeletionError — 発行できない状態
        VersioningError — object 未存在など
    """
    if not schema.is_valid_object_type(object_type):
        raise schema.VersioningError(f"invalid object_type: {object_type}")

    # スナップショットはロック外で構築（読み取りのみ）
    if object_type == schema.OBJECT_TYPE_COURSE:
        snapshot = build_course_snapshot(object_id)
    else:
        snapshot = build_document_snapshot(object_id)

    session = get_session()
    try:
        # state 行を用意してロック
        session.execute(
            sa_text("""
                INSERT INTO shared_version_state (object_type, object_id)
                VALUES (:ot, :oid)
                ON CONFLICT (object_type, object_id) DO NOTHING
            """),
            {"ot": object_type, "oid": object_id},
        )
        state = session.execute(
            sa_text("""
                SELECT lifecycle, latest_version_no
                FROM shared_version_state
                WHERE object_type = :ot AND object_id = :oid
                FOR UPDATE
            """),
            {"ot": object_type, "oid": object_id},
        ).fetchone()
        lifecycle = state[0]
        latest = int(state[1] or 0)
        if lifecycle == schema.LIFECYCLE_PURGED:
            raise schema.PurgedError("object has been purged")
        if lifecycle == schema.LIFECYCLE_PENDING_DELETION:
            raise schema.PendingDeletionError("object is scheduled for deletion")

        version_no = latest + 1
        rel = session.execute(
            sa_text("""
                INSERT INTO shared_versions
                    (object_type, object_id, version_no, snapshot, note, published_by)
                VALUES
                    (:ot, :oid, :vno, CAST(:snapshot AS jsonb), :note, CAST(:pub AS uuid))
                RETURNING id::text, created_at
            """),
            {
                "ot": object_type,
                "oid": object_id,
                "vno": version_no,
                "snapshot": json.dumps(snapshot, ensure_ascii=False),
                "note": note or "",
                "pub": published_by or None,
            },
        ).fetchone()
        release_id = rel[0]
        session.execute(
            sa_text("""
                UPDATE shared_version_state
                SET latest_version_no = :vno,
                    active_release_id = CAST(:rid AS uuid),
                    updated_at = now()
                WHERE object_type = :ot AND object_id = :oid
            """),
            {"vno": version_no, "rid": release_id, "ot": object_type, "oid": object_id},
        )
        session.commit()
    except schema.VersioningError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        logger.exception("publish_release failed: %s %s", object_type, object_id)
        raise
    finally:
        session.close()

    return {
        "id": release_id,
        "object_type": object_type,
        "object_id": object_id,
        "version_no": version_no,
        "note": note or "",
        "published_by": published_by,
        "created_at": rel[1].isoformat() if rel[1] else "",
        "snapshot": snapshot,
    }


# ---------------------------------------------------------------------------
# 参照
# ---------------------------------------------------------------------------

def _row_to_release(row, *, include_snapshot: bool = True) -> dict:
    out = {
        "id": str(row[0]),
        "object_type": row[1],
        "object_id": row[2],
        "version_no": int(row[3]),
        "note": row[4] or "",
        "published_by": str(row[5]) if row[5] else None,
        "created_at": row[6].isoformat() if row[6] else "",
    }
    if include_snapshot:
        out["snapshot"] = row[7] if isinstance(row[7], dict) else json.loads(row[7] or "{}")
    return out


def list_releases(object_type: str, object_id: str) -> list[dict]:
    """当該オブジェクトの Release を新しい順で返す（snapshot は含めない・一覧軽量化）。"""
    session = get_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, object_type, object_id, version_no, note, published_by, created_at
                FROM shared_versions
                WHERE object_type = :ot AND object_id = :oid
                ORDER BY version_no DESC
            """),
            {"ot": object_type, "oid": object_id},
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "id": str(r[0]), "object_type": r[1], "object_id": r[2],
            "version_no": int(r[3]), "note": r[4] or "",
            "published_by": str(r[5]) if r[5] else None,
            "created_at": r[6].isoformat() if r[6] else "",
        }
        for r in rows
    ]


def get_release(release_id: str) -> dict | None:
    session = get_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT id, object_type, object_id, version_no, note, published_by, created_at, snapshot
                FROM shared_versions
                WHERE id = CAST(:rid AS uuid)
                LIMIT 1
            """),
            {"rid": release_id},
        ).fetchone()
    finally:
        session.close()
    return _row_to_release(row) if row else None


def get_state(object_type: str, object_id: str) -> dict | None:
    session = get_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT object_type, object_id, active_release_id::text, latest_version_no,
                       lifecycle, delete_scheduled_at, delete_purge_after,
                       delete_scheduled_by::text, delete_reason, updated_at
                FROM shared_version_state
                WHERE object_type = :ot AND object_id = :oid
                LIMIT 1
            """),
            {"ot": object_type, "oid": object_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return None
    return {
        "object_type": row[0],
        "object_id": row[1],
        "active_release_id": row[2],
        "latest_version_no": int(row[3] or 0),
        "lifecycle": row[4],
        "delete_scheduled_at": row[5].isoformat() if row[5] else None,
        "delete_purge_after": row[6].isoformat() if row[6] else None,
        "delete_scheduled_by": row[7],
        "delete_reason": row[8] or "",
        "updated_at": row[9].isoformat() if row[9] else None,
    }
