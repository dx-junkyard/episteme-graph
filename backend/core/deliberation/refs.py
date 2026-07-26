"""ElementRef の解決（設計書 §2 / §7）。

要素型ごとに実体テーブル/artifact を索き、存在確認のうえ ``document_id`` /
``domain_key`` を補完した :class:`ElementRef` を返す。存在しなければ
:class:`ElementResolutionError` を投げる（API 層が 404/422 にマップ）。

DB は ``core.postgres.get_session`` 経由で直接読む（core は FastAPI/route を import しない）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core.deliberation.schema import (
    ELEMENT_EQUATION,
    ELEMENT_FIGURE,
    ELEMENT_SHARED_PART,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
    SCOPE_DOCUMENT,
    SCOPE_DOMAIN,
    ElementRef,
    ElementResolutionError,
)

# document_analysis_runs.stage_outputs の artifact ルートキー（persistence.ARTIFACTS_KEY）
_ARTIFACTS_KEY = "_artifacts"


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def document_run_artifacts(document_id: str) -> dict[str, Any]:
    """最新の completed run（無ければ最新 run）の ``stage_outputs._artifacts`` を返す。

    equation の解決・内訳、および positioning（面②）の intra-document レンズが
    共有で使う。run が無ければ空 dict。
    """
    if not str(document_id or "").strip():
        return {}
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT stage_outputs
                FROM document_analysis_runs
                WHERE document_id = :document_id
                ORDER BY (status = 'completed') DESC, updated_at DESC
                LIMIT 1
                """
            ),
            {"document_id": document_id},
        ).fetchone()
    finally:
        session.close()
    if not row or not isinstance(row[0], dict):
        return {}
    artifacts = row[0].get(_ARTIFACTS_KEY)
    return artifacts if isinstance(artifacts, dict) else {}


def equation_records(
    document_id: str, *, artifacts: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """document の equation_semantics artifact から equation レコード配列を返す（best-effort）。

    ``artifacts`` を渡すと ``document_run_artifacts`` の再取得を省略する（呼び出し側が
    既に同じ document の artifacts を取得済みの場合の二重 SELECT 回避。省略時
    （``None``、既定）は従来どおり自前で取得する — 既存呼び出し元は無変更で動く）。
    """
    if artifacts is None:
        artifacts = document_run_artifacts(document_id)
    eq_stage = artifacts.get("equation_semantics") if isinstance(artifacts, dict) else None
    if not isinstance(eq_stage, dict):
        return []
    records = eq_stage.get("equations")
    return [r for r in records if isinstance(r, dict)] if isinstance(records, list) else []


def _resolve_theory_claim(element_id: str) -> ElementRef:
    if not _is_uuid(element_id):
        raise ElementResolutionError(f"invalid theory_claim id: {element_id!r}", kind="invalid")
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                "SELECT document_id, chunk_id FROM theory_claims "
                "WHERE id = CAST(:id AS uuid) LIMIT 1"
            ),
            {"id": element_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise ElementResolutionError(f"theory_claim not found: {element_id}", kind="not_found")
    return ElementRef(
        scope=SCOPE_DOCUMENT,
        element_type=ELEMENT_THEORY_CLAIM,
        element_id=element_id,
        document_id=str(row[0] or ""),
        provenance={"chunk_id": str(row[1]) if row[1] else None},
    )


def _resolve_theory_component(element_id: str) -> ElementRef:
    if not _is_uuid(element_id):
        raise ElementResolutionError(f"invalid theory_component id: {element_id!r}", kind="invalid")
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                "SELECT course_id, source_scope FROM theory_components "
                "WHERE id = CAST(:id AS uuid) LIMIT 1"
            ),
            {"id": element_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise ElementResolutionError(f"theory_component not found: {element_id}", kind="not_found")
    scope_json = row[1] if isinstance(row[1], dict) else {}
    document_id = str(scope_json.get("document_id") or "")
    return ElementRef(
        scope=SCOPE_DOCUMENT,
        element_type=ELEMENT_THEORY_COMPONENT,
        element_id=element_id,
        document_id=document_id,
        provenance={"course_id": str(row[0]) if row[0] else None},
    )


def _resolve_figure(element_id: str) -> ElementRef:
    if not _is_uuid(element_id):
        raise ElementResolutionError(f"invalid figure id: {element_id!r}", kind="invalid")
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                "SELECT document_id, run_id, figure_key FROM document_figures "
                "WHERE id = CAST(:id AS uuid) LIMIT 1"
            ),
            {"id": element_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise ElementResolutionError(f"figure not found: {element_id}", kind="not_found")
    return ElementRef(
        scope=SCOPE_DOCUMENT,
        element_type=ELEMENT_FIGURE,
        element_id=element_id,
        document_id=str(row[0] or ""),
        provenance={
            "run_id": str(row[1]) if row[1] else None,
            "figure_key": str(row[2] or ""),
        },
    )


def _resolve_equation(element_id: str, document_id: str | None) -> ElementRef:
    # equation は独立テーブルを持たない（設計書 §2）。document_id が必須で、
    # その run の equations.json 内に equation_id が存在するかで確認する。
    if not str(document_id or "").strip():
        raise ElementResolutionError(
            "equation ElementRef requires document_id", kind="not_found"
        )
    records = equation_records(document_id)
    if not any(str(r.get("equation_id") or "") == str(element_id) for r in records):
        raise ElementResolutionError(
            f"equation not found in document {document_id}: {element_id}", kind="not_found"
        )
    return ElementRef(
        scope=SCOPE_DOCUMENT,
        element_type=ELEMENT_EQUATION,
        element_id=str(element_id),
        document_id=str(document_id),
    )


def _resolve_shared_part(element_id: str) -> ElementRef:
    if not _is_uuid(element_id):
        raise ElementResolutionError(f"invalid shared_part id: {element_id!r}", kind="invalid")
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                "SELECT domain_key, status FROM library_entries "
                "WHERE id = CAST(:id AS uuid) LIMIT 1"
            ),
            {"id": element_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise ElementResolutionError(f"shared_part not found: {element_id}", kind="not_found")
    return ElementRef(
        scope=SCOPE_DOMAIN,
        element_type=ELEMENT_SHARED_PART,
        element_id=element_id,
        domain_key=str(row[0] or ""),
        provenance={"status": str(row[1] or "")},
    )


def resolve(
    element_type: str,
    element_id: str,
    *,
    document_id: str | None = None,
    domain_key: str | None = None,
) -> ElementRef:
    """要素を解決して :class:`ElementRef` を返す。

    ``document_id`` は equation で必須（他型では row 側の値を正とし、渡された hint は無視）。
    """
    if element_type == ELEMENT_THEORY_CLAIM:
        ref = _resolve_theory_claim(element_id)
    elif element_type == ELEMENT_THEORY_COMPONENT:
        ref = _resolve_theory_component(element_id)
    elif element_type == ELEMENT_FIGURE:
        ref = _resolve_figure(element_id)
    elif element_type == ELEMENT_EQUATION:
        ref = _resolve_equation(element_id, document_id)
    elif element_type == ELEMENT_SHARED_PART:
        ref = _resolve_shared_part(element_id)
    else:
        raise ElementResolutionError(f"unknown element_type: {element_type!r}", kind="invalid")
    ref.validate()
    return ref
