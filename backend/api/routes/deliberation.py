"""W層（要素検討ワークスペース）API（実パス ``/api/admin/deliberation/...``）。

設計書 `docs/features/element_deliberation_workspace_design.md` §8。全経路 ``_require_teacher``。
document-scoped 要素は ``_ensure_document_viewable`` で fail-closed（W5）。

Phase 0（本ファイル現状）: 面①「内訳」の集約 overview のみ。面②「位置づけ」レンズ（§4.1/4.3/4.4）と
対話（§5・migration 046）は後続増分。core 側（`core.deliberation`）は FastAPI を import しない。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from dependencies import _require_teacher
from core.deliberation import decomposition, refs
from core.deliberation.schema import SCOPE_DOCUMENT, ElementResolutionError
from routes.theory_components import _ensure_document_viewable

router = APIRouter(prefix="/deliberation", tags=["deliberation"])


def _http_from_resolution_error(exc: ElementResolutionError) -> HTTPException:
    status = 422 if getattr(exc, "kind", "not_found") == "invalid" else 404
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/elements/{element_type}/{element_id}/overview")
def get_element_overview(
    element_type: str,
    element_id: str,
    document_id: str | None = Query(
        default=None,
        description="equation 要素で必須（独立テーブルを持たないため document で一意化）。"
        "他型では無視される。",
    ),
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """要素の面①内訳を集約して返す（非LLM・DB 非変更・設計書 §8）。

    - document-scoped（figure/theory_component/theory_claim/equation）は
      ``_ensure_document_viewable`` を通す（fail-closed・W5）。
    - domain-scoped（shared_part）は L層方針で本文テキストを教員全体に開示するため
      ``_require_teacher`` のみ（画像含有等の狭い開示は本エンドポイントの対象外）。
    - 数値（confidence 等）は返さない（W8。Phase 0 は集約のみで confidence 自体を持たない）。
    """
    try:
        ref = refs.resolve(element_type, element_id, document_id=document_id)
    except ElementResolutionError as exc:
        raise _http_from_resolution_error(exc) from exc

    if ref.scope == SCOPE_DOCUMENT:
        # 権限ゲート（fail-closed）。閲覧不可・不存在は 404。
        _ensure_document_viewable(ref.document_id or "", current_user)

    try:
        breakdown = decomposition.build(ref)
    except ElementResolutionError as exc:
        raise _http_from_resolution_error(exc) from exc

    return {
        "ref": ref.to_dict(),
        "decomposition": breakdown,
        # 面② 位置づけは次の増分（§4.1 論文内 / §4.3 分野の地図 / §4.4 承認・疑義）。
        "positioning": {
            "available": False,
            "note": "面② 位置づけレンズ（§4.1/4.3/4.4）は Phase 0 の次増分で追加",
        },
    }
