"""``deliberation_sessions`` / ``element_annotations`` の DB プリミティブ（設計書 §7・migration 049）。

``core/deliberation/identity_links.py`` と同じ形（生 SQL・try/finally で session.close()、
FastAPI/services 非import）。confidence の生値は本モジュールの戻り値に残るが（DB 界面）、
API 層は段階ラベルへ変換して返すこと（W8。``identity_links.confidence_label`` を再利用する）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core.deliberation.schema import (
    ANNOTATION_DECIDABLE_STATUSES,
    ANNOTATION_STATUS_CANDIDATE,
    ElementRef,
)


def _json(value: Any, default: Any) -> Any:
    return value if isinstance(value, type(default)) else default


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# deliberation_sessions
# ---------------------------------------------------------------------------

_SESSION_COLUMNS_SQL = """
    id::text, scope, element_type, element_id, document_id, domain_key,
    title, messages, created_by::text, created_at, updated_at
"""


def _session_row_to_dict(row: Any) -> dict:
    return {
        "id": str(row[0]),
        "scope": row[1] or "",
        "element_type": row[2] or "",
        "element_id": row[3] or "",
        "document_id": row[4],
        "domain_key": row[5],
        "title": row[6] or "",
        "messages": _json(row[7], []),
        "created_by": row[8],
        "created_at": row[9].isoformat() if row[9] else "",
        "updated_at": row[10].isoformat() if row[10] else "",
    }


def create_session(ref: ElementRef, *, title: str = "", created_by: str | None = None) -> dict:
    """対話セッションを1件作成する（設計書 §6）。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                INSERT INTO deliberation_sessions
                    (scope, element_type, element_id, document_id, domain_key, title, created_by)
                VALUES
                    (:scope, :element_type, :element_id, :document_id, :domain_key,
                     :title, CAST(:created_by AS uuid))
                RETURNING {_SESSION_COLUMNS_SQL}
                """
            ),
            {
                "scope": ref.scope,
                "element_type": ref.element_type,
                "element_id": ref.element_id,
                "document_id": ref.document_id,
                "domain_key": ref.domain_key,
                "title": title or "",
                "created_by": created_by,
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return _session_row_to_dict(row)


def get_session_by_id(session_id: str) -> dict | None:
    session = get_session()
    try:
        row = session.execute(
            sa_text(f"SELECT {_SESSION_COLUMNS_SQL} FROM deliberation_sessions WHERE id = CAST(:id AS uuid)"),
            {"id": session_id},
        ).fetchone()
    finally:
        session.close()
    return _session_row_to_dict(row) if row else None


def append_messages(session_id: str, new_messages: list[dict]) -> dict | None:
    """``messages`` JSONB へ追記する（P4: 追記のみ。既存要素は書き換えない）。

    セッションが存在しなければ ``None`` を返す（呼び出し側が 404 にマッピングする）。
    """
    if not new_messages:
        return get_session_by_id(session_id)
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                UPDATE deliberation_sessions
                SET messages = messages || CAST(:new_messages AS jsonb),
                    updated_at = now()
                WHERE id = CAST(:id AS uuid)
                RETURNING {_SESSION_COLUMNS_SQL}
                """
            ),
            {"id": session_id, "new_messages": _dump(new_messages)},
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return _session_row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# element_annotations
# ---------------------------------------------------------------------------

_ANNOTATION_COLUMNS_SQL = """
    id::text, scope, element_type, element_id, document_id, domain_key, session_id::text,
    kind, body, evidence, reason, confidence, status, committed_target,
    created_by::text, updated_by::text, created_at, updated_at
"""


def _annotation_row_to_dict(row: Any) -> dict:
    return {
        "id": str(row[0]),
        "scope": row[1] or "",
        "element_type": row[2] or "",
        "element_id": row[3] or "",
        "document_id": row[4],
        "domain_key": row[5],
        "session_id": row[6],
        "kind": row[7] or "",
        "body": _json(row[8], {}),
        "evidence": _json(row[9], []),
        "reason": row[10] or "",
        "confidence": row[11],  # 生値のまま(core は DB 界面。API 層で confidence_label へ)
        "status": row[12] or "",
        "committed_target": _json(row[13], {}),
        "created_by": row[14],
        "updated_by": row[15],
        "created_at": row[16].isoformat() if row[16] else "",
        "updated_at": row[17].isoformat() if row[17] else "",
    }


def create_annotation(
    ref: ElementRef,
    *,
    kind: str,
    body: dict,
    evidence: list,
    reason: str,
    confidence: float | None,
    session_id: str | None,
    created_by: str | None,
) -> dict:
    """候補注釈を1件作成する。常に ``status='candidate'`` 固定（W2: AI 出力は確定しない）。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                INSERT INTO element_annotations
                    (scope, element_type, element_id, document_id, domain_key, session_id,
                     kind, body, evidence, reason, confidence, status, created_by)
                VALUES
                    (:scope, :element_type, :element_id, :document_id, :domain_key,
                     CAST(:session_id AS uuid), :kind, CAST(:body AS jsonb),
                     CAST(:evidence AS jsonb), :reason, :confidence, :status,
                     CAST(:created_by AS uuid))
                RETURNING {_ANNOTATION_COLUMNS_SQL}
                """
            ),
            {
                "scope": ref.scope,
                "element_type": ref.element_type,
                "element_id": ref.element_id,
                "document_id": ref.document_id,
                "domain_key": ref.domain_key,
                "session_id": session_id,
                "kind": kind,
                "body": _dump(body or {}),
                "evidence": _dump(evidence or []),
                "reason": reason or "",
                "confidence": confidence,
                # status は引数として受け取らず、常にこの定数を束縛する(W2固定)。
                "status": ANNOTATION_STATUS_CANDIDATE,
                "created_by": created_by,
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return _annotation_row_to_dict(row)


def get_annotation(annotation_id: str) -> dict | None:
    session = get_session()
    try:
        row = session.execute(
            sa_text(f"SELECT {_ANNOTATION_COLUMNS_SQL} FROM element_annotations WHERE id = CAST(:id AS uuid)"),
            {"id": annotation_id},
        ).fetchone()
    finally:
        session.close()
    return _annotation_row_to_dict(row) if row else None


def list_annotations_for_element(
    element_type: str,
    element_id: str,
    *,
    document_id: str | None = None,
    domain_key: str | None = None,
) -> list[dict]:
    """要素に付いた候補・確定・却下すべての注釈を返す(P4)。

    document-scoped 要素は ``document_id`` で絞り込む(equation のように独立
    テーブルを持たない要素型は element_id が論文間で衝突しうるため。
    identity_links.list_for_instance と同じ理由・レビュー指摘 2026-07-15 と同型)。
    """
    session = get_session()
    try:
        if document_id:
            rows = session.execute(
                sa_text(
                    f"""
                    SELECT {_ANNOTATION_COLUMNS_SQL} FROM element_annotations
                    WHERE element_type = :element_type AND element_id = :element_id
                      AND document_id = :document_id
                    ORDER BY created_at ASC
                    """
                ),
                {"element_type": element_type, "element_id": element_id, "document_id": document_id},
            ).fetchall()
        else:
            rows = session.execute(
                sa_text(
                    f"""
                    SELECT {_ANNOTATION_COLUMNS_SQL} FROM element_annotations
                    WHERE element_type = :element_type AND element_id = :element_id
                      AND domain_key IS NOT DISTINCT FROM :domain_key
                      AND document_id IS NULL
                    ORDER BY created_at ASC
                    """
                ),
                {"element_type": element_type, "element_id": element_id, "domain_key": domain_key},
            ).fetchall()
    finally:
        session.close()
    return [_annotation_row_to_dict(r) for r in rows]


def set_annotation_status(
    annotation_id: str,
    *,
    status: str,
    committed_target: dict | None,
    updated_by: str | None,
) -> dict | None:
    """候補を確定(``committed``)/却下(``dismissed``)する。candidate からのみ遷移する。

    ``status`` は :data:`~core.deliberation.schema.ANNOTATION_DECIDABLE_STATUSES`
    (``committed``/``dismissed``)のみ受理する。対象行が存在しないか既に
    candidate でない(二重遷移)場合は ``None`` を返す(行削除はしない・P4。
    呼び出し側が 404/409 にマッピングする)。
    """
    if status not in ANNOTATION_DECIDABLE_STATUSES:
        raise ValueError(f"invalid status for set_annotation_status(): {status!r}")

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                f"""
                UPDATE element_annotations
                SET status = :status,
                    committed_target = CAST(:committed_target AS jsonb),
                    updated_by = CAST(:updated_by AS uuid),
                    updated_at = now()
                WHERE id = CAST(:id AS uuid) AND status = :current_status
                RETURNING {_ANNOTATION_COLUMNS_SQL}
                """
            ),
            {
                "id": annotation_id,
                "status": status,
                "committed_target": _dump(committed_target or {}),
                "updated_by": updated_by,
                "current_status": ANNOTATION_STATUS_CANDIDATE,
            },
        ).fetchone()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return _annotation_row_to_dict(row) if row else None
