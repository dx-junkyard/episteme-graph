"""状態投影 API（Status Projection, migration 038）。

admin ルータ配下（実パス /api/admin/status/...）。既存テーブルからの決定論的投影を
返すだけで、DB へは書き込まない。TEACHER 以上・自分の所有物のみ（S5）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

import services
from dependencies import _require_teacher
from core.postgres import get_session
from core.status import events as status_events
from core.status import projector

router = APIRouter(prefix="/status", tags=["Status"])


def _uid(current_user: dict) -> str:
    return current_user.get("id") or current_user.get("user_id")


@router.get("/overview")
def get_status_overview(current_user: dict = Depends(_require_teacher)):
    """自分の教材・コースの状態一覧（G層バッジと同じ更新規律。ポーリングしない）。"""
    uid = _uid(current_user)
    session = get_session()
    try:
        doc_ids = [
            r[0] for r in session.execute(
                sa_text(
                    "SELECT id::text FROM documents WHERE uploaded_by = CAST(:uid AS uuid) "
                    "ORDER BY updated_at DESC"
                ),
                {"uid": uid},
            ).fetchall()
        ]
        course_ids = [
            r[0] for r in session.execute(
                sa_text(
                    "SELECT id FROM learning_courses WHERE user_id = CAST(:uid AS uuid) "
                    "ORDER BY updated_at DESC"
                ),
                {"uid": uid},
            ).fetchall()
        ]
        materials = [projector.project_material_status(session, did).to_dict() for did in doc_ids]
        courses = [projector.project_course_status(session, cid).to_dict() for cid in course_ids]
    finally:
        session.close()
    return {"materials": materials, "courses": courses}


@router.get("/materials/{material_id}")
def get_material_status(material_id: str, current_user: dict = Depends(_require_teacher)):
    uid = _uid(current_user)
    if not services.user_owns_document(uid, material_id):
        raise HTTPException(status_code=404, detail="not found")
    session = get_session()
    try:
        return projector.project_material_status(session, material_id).to_dict()
    finally:
        session.close()


@router.get("/courses/{course_id}")
def get_course_status(course_id: str, current_user: dict = Depends(_require_teacher)):
    uid = _uid(current_user)
    if not services.user_owns_course(uid, course_id):
        raise HTTPException(status_code=404, detail="not found")
    session = get_session()
    try:
        return projector.project_course_status(session, course_id).to_dict()
    finally:
        session.close()


@router.get("/events")
def get_status_events(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    event_kind: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 100,
    current_user: dict = Depends(_require_teacher),
):
    """status_events の読み取り口（エージェント向け購読・KPI集計の下地）。

    entity_id を指定する場合は entity_type も必須（422）で、かつ所有者本人のみ（404）。
    entity_id を指定しない場合は、自分が所有する教材・コースのイベントに絞り込む
    （他ユーザーのイベントを返さない）。
    """
    uid = _uid(current_user)

    if entity_id and not entity_type:
        raise HTTPException(status_code=422, detail="entity_type is required when entity_id is given")

    session = get_session()
    try:
        if entity_id:
            if entity_type == "material" and not services.user_owns_document(uid, entity_id):
                raise HTTPException(status_code=404, detail="not found")
            if entity_type == "course" and not services.user_owns_course(uid, entity_id):
                raise HTTPException(status_code=404, detail="not found")
            if entity_type not in ("material", "course"):
                raise HTTPException(status_code=404, detail="not found")
            rows = status_events.list_events(
                session, entity_type=entity_type, entity_id=entity_id,
                event_kind=event_kind, since=since, limit=limit,
            )
            return {"events": rows}

        # entity_id 未指定: 自分が所有する教材・コースのイベントのみに絞り込む。
        doc_ids = {
            r[0] for r in session.execute(
                sa_text(
                    "SELECT id::text FROM documents WHERE uploaded_by = CAST(:uid AS uuid)"
                ),
                {"uid": uid},
            ).fetchall()
        }
        course_ids = {
            r[0] for r in session.execute(
                sa_text(
                    "SELECT id FROM learning_courses WHERE user_id = CAST(:uid AS uuid)"
                ),
                {"uid": uid},
            ).fetchall()
        }
        own_ids = doc_ids | course_ids
        if not own_ids:
            return {"events": []}
        rows = status_events.list_events(
            session, entity_type=entity_type, entity_ids=sorted(own_ids),
            event_kind=event_kind, since=since, limit=limit,
        )
        return {"events": rows}
    finally:
        session.close()
