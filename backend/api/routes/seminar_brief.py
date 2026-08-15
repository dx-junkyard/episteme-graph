"""ゼミ前ブリーフ（Seminar Brief, 提案1 v1）API。

実パス: GET /api/admin/documents/{document_ref}/seminar-brief
（admin 系子ルーターとして main.py から prefix="/api/admin" で直接登録される —
routes/reconstruction.py の admin_router と同型）。

正本: docs/features/seminar_brief_mirroring_design.md §1（SB1〜SB4）。
読み時合成のみ（書き込みなし・LLM 0回・migration 不要）。合成の実体は
``core/doubt/seminar_brief.py::build_seminar_brief``（FastAPI 非 import）。

権限は2段ゲート:
  1. ``_require_teacher``（TEACHER 以上）
  2. ``_ensure_document_viewable``（document の閲覧権。不可視は 404 fail-closed）
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from dependencies import _require_teacher
from routes.theory_components import _ensure_document_viewable
from core.doubt.seminar_brief import build_seminar_brief
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)

# main.py で prefix="/api/admin" を付けて直接登録される admin 系子ルーター（prefix なし）
admin_router = APIRouter(tags=["SeminarBrief"])


@admin_router.get("/documents/{document_ref}/seminar-brief")
def get_seminar_brief(
    document_ref: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """ゼミ前ブリーフ（4区画の read-only 合成ビュー）を返す。

    区画: ①脆い前提（未検証×下流影響「高」・段階ラベルのみ）②一点吊りの支持線
    （level=single の事実文）③晴れ間（閉世界の固定事実文, SL1）④学習者からの問い
    （v1 は空欄予約, SB3）。数値の生値は返さない（SB2）。

    document / course 対応が解決できない場合は
    ``{"available": false, "reason": ...}`` の正直縮退で 200 を返す
    （閲覧権が無い・document 実体が無い場合は ``_ensure_document_viewable`` の 404）。
    """
    _ensure_document_viewable(document_ref, current_user)
    session = _pg_session()
    try:
        return build_seminar_brief(session, document_ref)
    finally:
        session.close()
