"""構造の降下路 API（Phase 3 — 足場ダイヤル・楽屋 v1、読み取り専用）。

正本: ``docs/features/structure_descent_design.md``（SD1〜SD6）。実パスは
``/api/learning/courses/{course_id}/descent/...``（本人のみ・受講ゲートは
``get_accessible_course_data``、cycle / reconstruction と同型）。

- **GET 2本のみ**（書き込みメソッドなし）。**閲覧をサーバに記録しない** —
  開示履歴・使用数の集計を持たない（SD3/SD5。段を引くのは常に本人のダイヤルで、
  サーバは全段を一度に返すだけ — SD1）。
- 実体は ``core/descent/``（非LLM・決定論 — SD2）。導出失敗は 500 にせず
  fail-closed に縮退する（梯子は ``{available: false}``、楽屋は宣言一行 + 空 steps）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from dependencies import _get_current_user
from services import get_accessible_course_data
from core.descent import (
    BACKSTAGE_DECLARATION,
    SUPPORTED_ELEMENT_TYPES,
    build_backstage_path,
    build_ladder,
)

logger = logging.getLogger(__name__)

# main.py で直接 include される学習者向け router（cycle.py と同型。
# admin.router 経由の二段ネストにしない — Tier 3-17c）。
learning_router = APIRouter(prefix="/api/learning", tags=["Learning"])


def _require_course(user_id: str, course_id: str) -> dict:
    course_data = get_accessible_course_data(user_id, course_id)
    if not course_data:
        raise HTTPException(status_code=404, detail="Course not found")
    return course_data


@learning_router.get("/courses/{course_id}/descent/ladder")
def get_descent_ladder(
    course_id: str,
    element_type: str = Query(...),
    element_id: str = Query(...),
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """足場ダイヤルの梯子（想起プロンプト → stage 骨格事実文 → 記号 → 出典リビール）。

    ``element_type`` は equation / component / claim のみ（それ以外は 422）。要素の
    解決はコース sources 内に限定され（fail-closed）、解決できなければ
    ``{"available": false}``。全段を一度に返し、開示順の制御はフロントが持つ（SD1。
    サーバは開示履歴を記録しない）。
    """
    if element_type not in SUPPORTED_ELEMENT_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported element_type")
    course_data = _require_course(current_user["id"], course_id)
    try:
        return build_ladder(course_data, course_id, element_type, element_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "descent ladder build failed course=%s %s:%s",
            course_id, element_type, element_id, exc_info=True,
        )
        return {"available": False}


@learning_router.get("/courses/{course_id}/descent/backstage-path")
def get_descent_backstage_path(
    course_id: str,
    element_type: str = Query(...),
    element_id: str = Query(...),
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """楽屋の降下路（notation_patterns → 記号定義 → 前提概念の generic 説明）。

    宣言一行「ここでの質問と閲覧は集計に入りません。記録はあなたにだけ残ります」を
    常設で返す（SD6 と同系の「宣言された留保」）。素材の無い step は出さない。
    **この GET はサーバに何も書かない**（閲覧を記録しない — SD4/SD5）。
    """
    if element_type not in SUPPORTED_ELEMENT_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported element_type")
    course_data = _require_course(current_user["id"], course_id)
    try:
        return build_backstage_path(course_data, course_id, element_type, element_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "descent backstage path build failed course=%s %s:%s",
            course_id, element_type, element_id, exc_info=True,
        )
        return {"declaration": BACKSTAGE_DECLARATION, "steps": []}
