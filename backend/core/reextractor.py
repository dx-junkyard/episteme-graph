"""再抽出ワークフロー — スキーマ更新後に既存教材を非同期で再処理する。

スキーマ提案が承認された後、既存の教材（documents）に対して
バックグラウンドで再抽出ジョブを実行し、新しいスキーマ定義で
ナレッジグラフを再構築する。

Usage::

    from core.reextractor import enqueue_reextraction, get_job_status

    job = enqueue_reextraction(proposal_id="abc123")
    status = get_job_status(job["job_id"])
"""

from __future__ import annotations

import json
import logging
import threading
import uuid

from sqlalchemy import text as sa_text

from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)


def enqueue_reextraction(proposal_id: str) -> dict:
    """再抽出ジョブをキューに追加する。

    処理対象は status='completed' の全ドキュメント。
    ジョブはバックグラウンドスレッドで非同期実行される。
    """
    session = _pg_session()
    try:
        # 処理対象ドキュメント数を取得
        count_row = session.execute(
            sa_text("SELECT COUNT(*) FROM documents WHERE status = 'completed'")
        ).fetchone()
        total_docs = count_row[0] if count_row else 0

        job_id = str(uuid.uuid4())[:12]
        session.execute(
            sa_text("""
                INSERT INTO reextraction_jobs (id, proposal_id, status, total_docs)
                VALUES (:id, :proposal_id, 'pending', :total_docs)
            """),
            {"id": job_id, "proposal_id": proposal_id, "total_docs": total_docs},
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    # バックグラウンドスレッドで実行
    thread = threading.Thread(
        target=_run_reextraction_job,
        args=(job_id,),
        daemon=True,
    )
    thread.start()

    logger.info(
        "Reextraction job %s enqueued (proposal=%s, docs=%d)",
        job_id, proposal_id, total_docs,
    )

    return {
        "job_id": job_id,
        "proposal_id": proposal_id,
        "status": "pending",
        "total_docs": total_docs,
        "processed_docs": 0,
    }


def _run_reextraction_job(job_id: str) -> None:
    """再抽出ジョブを実行する（バックグラウンドスレッド）。"""
    from services import (
        build_knowledge_graph,
        chunk_text,
        embed_chunks,
        extract_pdf_text,
    )
    from core.storage import get_storage_client

    # ステータスを running に更新
    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                UPDATE reextraction_jobs
                SET status = 'running', started_at = now()
                WHERE id = :id
            """),
            {"id": job_id},
        )
        session.commit()
    except Exception:
        session.rollback()
        return
    finally:
        session.close()

    # 処理対象ドキュメント取得
    session = _pg_session()
    try:
        docs = session.execute(
            sa_text("""
                SELECT id, title, filename, source_path
                FROM documents
                WHERE status = 'completed' AND filename IS NOT NULL
                ORDER BY created_at
            """),
        ).fetchall()
    finally:
        session.close()

    processed = 0
    errors = []

    for doc in docs:
        doc_id = str(doc[0])
        title = doc[1] or ""
        filename = doc[2] or ""
        material_id = doc[3] or ""

        try:
            # MinIOからPDFを取得してテキスト抽出
            try:
                storage = get_storage_client()
                pdf_bytes = storage.get_object("raw-papers", filename)
                text = extract_pdf_text(pdf_bytes)
            except Exception:
                logger.warning(
                    "Could not retrieve PDF for doc %s from MinIO, skipping",
                    doc_id,
                )
                continue

            # ナレッジグラフを再構築
            knowledge_graph = build_knowledge_graph(text, title)

            # チャンクを再生成・再埋め込み
            chunks = chunk_text(text, chunk_size=1000, overlap=100)

            # 既存チャンクを削除
            session = _pg_session()
            try:
                session.execute(
                    sa_text("DELETE FROM chunks WHERE document_id = CAST(:doc_id AS uuid)"),
                    {"doc_id": doc_id},
                )
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()

            # 新しいチャンクを埋め込み
            if chunks:
                embed_chunks(material_id, doc_id, chunks)

            # ドキュメントを更新
            session = _pg_session()
            try:
                session.execute(
                    sa_text("""
                        UPDATE documents
                        SET knowledge_graph = CAST(:kg AS jsonb),
                            text_length = :text_length,
                            chunk_count = :chunk_count,
                            updated_at = now()
                        WHERE id = CAST(:doc_id AS uuid)
                    """),
                    {
                        "doc_id": doc_id,
                        "kg": json.dumps(knowledge_graph, ensure_ascii=False),
                        "text_length": len(text),
                        "chunk_count": len(chunks),
                    },
                )
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

            processed += 1
            logger.info("Reextracted doc %s (%d/%d)", doc_id, processed, len(docs))

            # 進捗を更新
            session = _pg_session()
            try:
                session.execute(
                    sa_text("""
                        UPDATE reextraction_jobs
                        SET processed_docs = :processed
                        WHERE id = :id
                    """),
                    {"id": job_id, "processed": processed},
                )
                session.commit()
            except Exception:
                session.rollback()
            finally:
                session.close()

        except Exception as exc:
            logger.warning("Reextraction failed for doc %s: %s", doc_id, exc)
            errors.append(f"{doc_id}: {exc}")

    # 完了ステータスを更新
    final_status = "completed" if not errors else "completed"
    error_msg = "; ".join(errors[:10]) if errors else None

    session = _pg_session()
    try:
        session.execute(
            sa_text("""
                UPDATE reextraction_jobs
                SET status = :status,
                    processed_docs = :processed,
                    error_message = :error_message,
                    completed_at = now()
                WHERE id = :id
            """),
            {
                "id": job_id,
                "status": final_status,
                "processed": processed,
                "error_message": error_msg,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()

    logger.info(
        "Reextraction job %s finished: %d/%d docs, %d errors",
        job_id, processed, len(docs), len(errors),
    )


def get_jobs(limit: int = 20) -> list[dict]:
    """再抽出ジョブ一覧を取得する。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, proposal_id, status, total_docs, processed_docs,
                       error_message, started_at, completed_at, created_at
                FROM reextraction_jobs
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"limit": limit},
        ).fetchall()
    finally:
        session.close()

    return [
        {
            "job_id": r[0],
            "proposal_id": r[1],
            "status": r[2],
            "total_docs": r[3],
            "processed_docs": r[4],
            "error_message": r[5],
            "started_at": r[6].isoformat() if r[6] else "",
            "completed_at": r[7].isoformat() if r[7] else "",
            "created_at": r[8].isoformat() if r[8] else "",
        }
        for r in rows
    ]


def get_job_status(job_id: str) -> dict | None:
    """特定の再抽出ジョブのステータスを取得する。"""
    session = _pg_session()
    try:
        r = session.execute(
            sa_text("""
                SELECT id, proposal_id, status, total_docs, processed_docs,
                       error_message, started_at, completed_at, created_at
                FROM reextraction_jobs
                WHERE id = :id
            """),
            {"id": job_id},
        ).fetchone()
    finally:
        session.close()

    if not r:
        return None

    return {
        "job_id": r[0],
        "proposal_id": r[1],
        "status": r[2],
        "total_docs": r[3],
        "processed_docs": r[4],
        "error_message": r[5],
        "started_at": r[6].isoformat() if r[6] else "",
        "completed_at": r[7].isoformat() if r[7] else "",
        "created_at": r[8].isoformat() if r[8] else "",
    }
