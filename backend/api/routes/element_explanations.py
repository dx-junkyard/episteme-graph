"""要素説明（``element_explanations``）の承認 API（Phase 2, migration 056）。

正本: ``docs/features/hierarchical_context_explanation_design.md`` §5.2。

パイプライン（ContextualExplanationAgent 等・後続実装）が書く candidate を教員が
確認・承認・却下・編集するための最小 API。DELETE endpoint は作らない（P4。行削除は
document 削除経路の明示 DELETE に将来同乗させる想定 — 本エージェントのスコープ外。
「未配線」のまま報告する）。

セッション管理は ``core.atlas_store`` 系ルータと同型: ``core.element_explanations`` の
各関数は渡された session に対して素の SQL を発行するだけでコミット/ロールバックしない
ため、ここ（route 層）で1トランザクションとして束ねる。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from dependencies import _require_teacher
from core import element_explanations as store
from core.deliberation.identity_links import confidence_label
from core.postgres import get_session
from core.schema import AUDIT_ENTITY_ELEMENT_EXPLANATION
from routes.theory_components import _ensure_document_editable, _ensure_document_viewable
from services import record_review_event


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["element-explanations"])

# 一括承認/却下の1回あたり上限（正規化後の explanation_ids 件数）。
BULK_REVIEW_MAX_ITEMS = 200

_BULK_ACTION_TO_STATUS = {
    "approve": store.STATUS_APPROVED,
    "dismiss": store.STATUS_DISMISSED,
}


def _canonical_document_id(chunks: list[dict], fallback: str) -> str:
    for chunk in chunks:
        if chunk.get("document_id"):
            return str(chunk["document_id"])
    return fallback


def _public_row(row: dict) -> dict:
    """API 応答用に confidence 生値を除去した1件を返す（E6: 段階ラベルのみ）。"""
    evidence = dict(row.get("evidence") or {})
    confidence_raw = evidence.pop("confidence", None)
    evidence["confidence_label"] = confidence_label(confidence_raw)
    return {
        "id": row.get("id"),
        "document_id": row.get("document_id"),
        "element_type": row.get("element_type"),
        "element_id": row.get("element_id"),
        "kind": row.get("kind"),
        "body": row.get("body"),
        "evidence": evidence,
        "status": row.get("status"),
        "created_by": row.get("created_by"),
        "reviewed_by": row.get("reviewed_by"),
        "reviewed_at": row.get("reviewed_at"),
        "created_at": row.get("created_at"),
    }


@router.get("/documents/{document_id}/element-explanations")
def list_document_element_explanations(
    document_id: str,
    element_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    chunks = _ensure_document_viewable(document_id, current_user)
    canonical_document_id = _canonical_document_id(chunks, document_id)
    session = get_session()
    try:
        rows = store.list_for_document(
            session,
            canonical_document_id,
            element_type=element_type,
            status=status,
            kind=kind,
        )
    finally:
        session.close()
    return {"explanations": [_public_row(r) for r in rows]}


@router.post("/element-explanations/{explanation_id}/approve")
def approve_element_explanation(
    explanation_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    session = get_session()
    try:
        existing = store.get_by_id(session, explanation_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Element explanation not found")
        _ensure_document_editable(existing["document_id"], current_user)
        updated = store.approve(session, explanation_id, current_user.get("id"))
        if updated is None:
            raise HTTPException(status_code=404, detail="Element explanation not found")
        session.commit()
    except store.ElementExplanationError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    record_review_event(
        AUDIT_ENTITY_ELEMENT_EXPLANATION,
        explanation_id,
        existing.get("status", ""),
        "approved",
        current_user.get("id"),
        {
            "action": "element_explanation.approve",
            "document_id": existing.get("document_id"),
            "element_type": existing.get("element_type"),
            "element_id": existing.get("element_id"),
            "kind": existing.get("kind"),
        },
    )
    return {"explanation": _public_row(updated)}


@router.post("/element-explanations/{explanation_id}/dismiss")
def dismiss_element_explanation(
    explanation_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    session = get_session()
    try:
        existing = store.get_by_id(session, explanation_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Element explanation not found")
        _ensure_document_editable(existing["document_id"], current_user)
        updated = store.dismiss(session, explanation_id, current_user.get("id"))
        if updated is None:
            raise HTTPException(status_code=404, detail="Element explanation not found")
        session.commit()
    except store.ElementExplanationError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    record_review_event(
        AUDIT_ENTITY_ELEMENT_EXPLANATION,
        explanation_id,
        existing.get("status", ""),
        "dismissed",
        current_user.get("id"),
        {
            "action": "element_explanation.dismiss",
            "document_id": existing.get("document_id"),
            "element_type": existing.get("element_type"),
            "element_id": existing.get("element_id"),
            "kind": existing.get("kind"),
        },
    )
    return {"explanation": _public_row(updated)}


class ElementExplanationBulkReview(BaseModel):
    action: str
    explanation_ids: list[str]


@router.post("/documents/{document_id}/element-explanations/bulk-review")
def bulk_review_element_explanations(
    document_id: str,
    body: ElementExplanationBulkReview,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """一括承認・一括却下（``candidate → approved/dismissed``）。教員のレビュー負荷軽減用。

    candidate-only 原則は維持する（``core.element_explanations.bulk_transition`` 参照）。
    1件の競合や不正な id が混ざっていても全体を失敗させない部分成功セマンティクスで、
    遷移できなかった行は ``skipped`` に理由付きで正直に返す（P4）。DELETE endpoint は
    作らない。
    """
    new_status = _BULK_ACTION_TO_STATUS.get(body.action)
    if new_status is None:
        raise HTTPException(
            status_code=422,
            detail=f"invalid action: {body.action!r} (must be 'approve' or 'dismiss')",
        )

    ids: list[str] = []
    seen: set[str] = set()
    for raw in body.explanation_ids or []:
        normalized = str(raw or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ids.append(normalized)
    if not ids:
        raise HTTPException(status_code=422, detail="explanation_ids is required")
    if len(ids) > BULK_REVIEW_MAX_ITEMS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"too many explanation_ids: {len(ids)} exceeds limit "
                f"{BULK_REVIEW_MAX_ITEMS}"
            ),
        )

    chunks = _ensure_document_editable(document_id, current_user)
    canonical_document_id = _canonical_document_id(chunks, document_id)

    session = get_session()
    try:
        result = store.bulk_transition(
            session,
            canonical_document_id,
            ids,
            new_status=new_status,
            user_id=current_user.get("id"),
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    action_label = f"element_explanation.{body.action}"
    for row in result["updated"]:
        record_review_event(
            AUDIT_ENTITY_ELEMENT_EXPLANATION,
            row.get("id"),
            "candidate",
            row.get("status", ""),
            current_user.get("id"),
            {
                "action": action_label,
                "document_id": row.get("document_id"),
                "element_type": row.get("element_type"),
                "element_id": row.get("element_id"),
                "kind": row.get("kind"),
                "bulk": True,
            },
        )

    return {
        "updated": [_public_row(r) for r in result["updated"]],
        "skipped": result["skipped"],
    }


class ElementExplanationBodyPatch(BaseModel):
    body: str


@router.patch("/element-explanations/{explanation_id}")
def update_element_explanation_body(
    explanation_id: str,
    patch: ElementExplanationBodyPatch,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """本文を編集する。旧行は ``superseded`` に遷移し、新 revision 行を作る
    （``core.element_explanations.update_body`` 参照。履歴保持・P4）。"""
    session = get_session()
    try:
        existing = store.get_by_id(session, explanation_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Element explanation not found")
        _ensure_document_editable(existing["document_id"], current_user)
        new_row = store.update_body(
            session, explanation_id, current_user.get("id"), patch.body,
        )
        if new_row is None:
            raise HTTPException(status_code=404, detail="Element explanation not found")
        session.commit()
    except store.ElementExplanationError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    record_review_event(
        AUDIT_ENTITY_ELEMENT_EXPLANATION,
        new_row.get("id", explanation_id),
        existing.get("status", ""),
        new_row.get("status", ""),
        current_user.get("id"),
        {
            "action": "element_explanation.edit",
            "document_id": existing.get("document_id"),
            "element_type": existing.get("element_type"),
            "element_id": existing.get("element_id"),
            "kind": existing.get("kind"),
            "superseded_id": explanation_id,
        },
    )
    return {"explanation": _public_row(new_row)}
