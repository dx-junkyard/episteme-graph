"""W層（要素検討ワークスペース）API（実パス ``/api/admin/deliberation/...``）。

設計書 `docs/features/element_deliberation_workspace_design.md` §8。全経路 ``_require_teacher``。
document-scoped 要素は ``_ensure_document_viewable``/``_ensure_document_editable`` で
fail-closed（W5）。

Phase 0（overview）: 面①「内訳」+ 面②「位置づけ」（§4.1/4.3/4.4 のみ・§4.2 コーパス横断は
Phase 1）の集約 overview。

Phase W-β（本増分）: 同一性リンク（`element_identity_links`、migration 048）の
作成・確定・却下・一覧。設計は `docs/features/knowledge_network_vision.md`
（§3 修正② / §4 KN-2・KN-3）と本設計書 §5.5。LLM 呼び出しはゼロ（人間操作のみ）。
確定・却下は常に候補（`status='candidate'`）からの人間の判断（KN-3）で、
行削除 API は作らない（W4）。

core 側（`core.deliberation`）は FastAPI を import しない。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from dependencies import _require_teacher
from core.deliberation import decomposition, identity_links, positioning, refs
from core.deliberation.schema import (
    ELEMENT_SHARED_PART,
    IDENTITY_LINK_STATUS_CONFIRMED,
    IDENTITY_LINK_STATUS_REJECTED,
    SCOPE_DOCUMENT,
    ElementResolutionError,
)
from core.schema import AUDIT_ENTITY_DELIBERATION
from routes.theory_components import _ensure_document_editable, _ensure_document_viewable
from services import record_review_event, resolve_document_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/deliberation", tags=["deliberation"])


def _http_from_resolution_error(exc: ElementResolutionError) -> HTTPException:
    status = 422 if getattr(exc, "kind", "not_found") == "invalid" else 404
    return HTTPException(status_code=status, detail=str(exc))


def _identity_link_response(link: dict[str, Any]) -> dict[str, Any]:
    """同一性リンク行を API 応答用に整形する（W8: confidence は生値を返さずラベルのみ）。"""
    response = dict(link)
    raw_confidence = response.pop("confidence", None)
    response["confidence_label"] = identity_links.confidence_label(raw_confidence)
    return response


# ---------------------------------------------------------------------------
# shared_part（domain-scoped）の document 保護フィールドのフィルタ（W5 / レビュー指摘）
# ---------------------------------------------------------------------------
#
# L層方針（設計書 W5）: library_entry の本文（テキスト）は教員全体に開示するが、
# 例示画像（exemplar_images）は由来 document の権限を継承し、非閲覧者には見せない。
# フィルタ判定自体は per-document の権限解決（`services.resolve_document_access`）が
# 要るため、FastAPI/services を import できない core/deliberation 側ではなくこの
# route 層で行う（開発ルール2）。純粋なフィルタ関数（`_filter_by_document_view`）は
# `can_view(document_id) -> bool` を注入で受け取るだけにして DB 非依存でテストできる
# ようにし、実際の DB 判定はここでの呼び出し側だけが持つ。


def _filter_by_document_view(
    items: list[dict[str, Any]],
    document_id_key: str,
    can_view: Callable[[str], bool],
) -> tuple[list[dict[str, Any]], int]:
    """``items`` を ``item[document_id_key]`` の閲覧権限でフィルタする。

    戻り値は ``(閲覧可能な要素のみのリスト, 隠した件数)``。P4「情報を落とさない」・
    「出所の正直さ」に沿い、隠したことを黙って欠落させず件数として呼び出し側に返す。
    """
    visible: list[dict[str, Any]] = []
    hidden = 0
    for item in items:
        doc_id = str((item or {}).get(document_id_key) or "")
        if doc_id and can_view(doc_id):
            visible.append(item)
        else:
            hidden += 1
    return visible, hidden


def _make_document_view_checker(current_user: dict) -> Callable[[str], bool]:
    """``resolve_document_access`` をユニーク document_id ごとに1回だけ呼ぶ
    ``can_view(document_id) -> bool`` を返す（N+1 回避）。"""
    uid = current_user.get("id") or current_user.get("user_id")
    cache: dict[str, bool] = {}

    def can_view(document_id: str) -> bool:
        if document_id not in cache:
            cache[document_id] = resolve_document_access(uid, document_id).can_view
        return cache[document_id]

    return can_view


def _apply_exemplar_image_gate(breakdown: dict[str, Any], current_user: dict) -> None:
    """shared_part の面①内訳（``frozen_content.exemplar_images``）を、各画像の由来
    document の閲覧権限でフィルタする（W5: 画像は由来 document の権限を継承。
    本文テキストである他のフィールドはそのまま教員全体に開示する）。

    隠した件数は ``fields.exemplar_images_hidden_count`` として正直に返す
    （黙って欠落させない）。
    """
    fields = breakdown.get("fields")
    if not isinstance(fields, dict):
        return
    frozen_content = fields.get("frozen_content")
    if not isinstance(frozen_content, dict):
        return
    images = frozen_content.get("exemplar_images")
    if not isinstance(images, list) or not images:
        return
    visible, hidden = _filter_by_document_view(
        images, "source_document_id", _make_document_view_checker(current_user)
    )
    frozen_content["exemplar_images"] = visible
    fields["exemplar_images_hidden_count"] = hidden


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

    if ref.element_type == ELEMENT_SHARED_PART:
        # L層方針（W5）: 本文は教員全体に開示するが、例示画像は由来 document の権限を
        # 継承する。凍結版スナップショット（frozen_content）に紛れ込む exemplar_images を
        # ここでフィルタする（レビュー指摘: 従来は無条件で返していた）。
        _apply_exemplar_image_gate(breakdown, current_user)

    # 面② 位置づけ（§4.1 論文内 / §4.3 分野の地図 / §4.4 承認・疑義）。positioning 全体が
    # 失敗しても overview 自体は面①内訳だけで返す（レンズ単位の fail-soft は positioning.py 側）。
    try:
        positioning_payload: dict[str, Any] = {
            "available": True,
            "lenses": positioning.build(ref),
        }
    except Exception:  # noqa: BLE001
        logger.warning(
            "deliberation positioning failed for %s:%s", ref.element_type, ref.element_id, exc_info=True
        )
        positioning_payload = {
            "available": False,
            "note": "位置づけレンズの取得に失敗したため内訳のみ返す",
        }

    return {
        "ref": ref.to_dict(),
        "decomposition": breakdown,
        "positioning": positioning_payload,
    }


# ---------------------------------------------------------------------------
# Phase W-β: 同一性リンク（element_identity_links）
# ---------------------------------------------------------------------------


class IdentityLinkCreateRequest(BaseModel):
    """同一性リンク候補の作成リクエスト（設計書 §8）。confidence は受け取らない
    （本エンドポイントは人間操作起点。LLM 候補の confidence 付き作成は Phase W-2 の
    対話ループが `core.deliberation.identity_links.create_candidate` を直接呼ぶ想定）。
    """

    instance_element_type: str
    instance_element_id: str
    document_id: str | None = Field(
        default=None,
        description="equation 要素で必須（refs.resolve の一意化に使う）。他型では無視される。",
    )
    shared_part_id: str
    local_expression: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Any] = Field(default_factory=list)
    reason: str = ""


@router.post("/identity-links")
def create_identity_link(
    body: IdentityLinkCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """インスタンス要素 ↔ 共通部品（shared_part）の同一性リンク候補を1件作成する。

    常に ``status='candidate'``（KN-3）。インスタンス側の表記は書き換えない（KN-2）。
    解釈の付与は editor 以上（W5）— ``_ensure_document_editable`` を通す。
    """
    try:
        instance_ref = refs.resolve(
            body.instance_element_type, body.instance_element_id, document_id=body.document_id
        )
    except ElementResolutionError as exc:
        raise _http_from_resolution_error(exc) from exc
    if instance_ref.scope != SCOPE_DOCUMENT:
        raise HTTPException(
            status_code=422,
            detail="identity link source must be a document-scoped instance element",
        )
    _ensure_document_editable(instance_ref.document_id or "", current_user)

    try:
        shared_part_ref = refs.resolve(ELEMENT_SHARED_PART, body.shared_part_id)
    except ElementResolutionError as exc:
        raise _http_from_resolution_error(exc) from exc

    link = identity_links.create_candidate(
        instance_ref,
        shared_part_ref.element_id,
        local_expression=body.local_expression,
        evidence=body.evidence,
        reason=body.reason,
        created_by=current_user.get("id"),
    )
    record_review_event(
        AUDIT_ENTITY_DELIBERATION,
        link["id"],
        "",
        link["status"],
        current_user.get("id"),
        {
            "action": "identity_link.create",
            "instance_element_type": instance_ref.element_type,
            "instance_element_id": instance_ref.element_id,
            "instance_document_id": instance_ref.document_id,
            "shared_part_id": shared_part_ref.element_id,
        },
    )
    return _identity_link_response(link)


def _decide_identity_link(link_id: str, status: str, current_user: dict) -> dict[str, Any]:
    existing = identity_links.get_by_id(link_id)
    if not existing:
        raise HTTPException(status_code=404, detail="identity link not found")
    _ensure_document_editable(existing["instance_document_id"], current_user)

    result = identity_links.decide(link_id, status=status, decided_by=current_user.get("id"))
    if result is None:
        raise HTTPException(status_code=409, detail="identity link already decided")

    record_review_event(
        AUDIT_ENTITY_DELIBERATION,
        link_id,
        existing["status"],
        status,
        current_user.get("id"),
        {"action": f"identity_link.{status}"},
    )
    return _identity_link_response(result)


@router.post("/identity-links/{link_id}/confirm")
def confirm_identity_link(
    link_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """同一性リンク候補を確定する（KN-3: 人間のみ。行削除はしない・W4）。"""
    return _decide_identity_link(link_id, IDENTITY_LINK_STATUS_CONFIRMED, current_user)


@router.post("/identity-links/{link_id}/reject")
def reject_identity_link(
    link_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """同一性リンク候補を却下する（status 遷移で保持・P4。行削除はしない）。"""
    return _decide_identity_link(link_id, IDENTITY_LINK_STATUS_REJECTED, current_user)


@router.get("/elements/{element_type}/{element_id}/identity-links")
def list_identity_links_for_element(
    element_type: str,
    element_id: str,
    document_id: str | None = Query(
        default=None,
        description="equation 要素で必須。他型では無視される。",
    ),
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """インスタンス要素に付いた同一性リンク一覧（候補・確定・却下すべて。P4）。

    shared_part（domain-scoped）は別エンドポイント（``GET /shared-parts/{id}/identity-links``）
    を使う（L層の開示方針が異なるため。§5 W5）。
    """
    try:
        ref = refs.resolve(element_type, element_id, document_id=document_id)
    except ElementResolutionError as exc:
        raise _http_from_resolution_error(exc) from exc
    if ref.scope != SCOPE_DOCUMENT:
        raise HTTPException(
            status_code=422,
            detail="use GET /shared-parts/{shared_part_id}/identity-links for shared_part elements",
        )
    _ensure_document_viewable(ref.document_id or "", current_user)

    links = identity_links.list_for_instance(ref.element_type, ref.element_id, ref.document_id or "")
    return {
        "ref": ref.to_dict(),
        "identity_links": [_identity_link_response(link) for link in links],
    }


@router.get("/shared-parts/{shared_part_id}/identity-links")
def list_identity_links_for_shared_part(
    shared_part_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """共通部品（library_entry）に紐づく同一性リンク一覧。

    L層の開示方針（本文テキストは教員全体に開示）を踏襲し ``_require_teacher`` のみ
    だが、各リンクは由来論文（``instance_document_id``）の情報を含む（どの論文の
    どの表現かという由来 document 由来の情報）ため、各インスタンス document の
    閲覧権限が無いリクエスト者には見せない（レビュー指摘: 従来は無条件で返していた）。
    隠した件数は ``hidden_count`` として正直に返す（黙って欠落させない・P4/出所の正直さ）。
    """
    try:
        ref = refs.resolve(ELEMENT_SHARED_PART, shared_part_id)
    except ElementResolutionError as exc:
        raise _http_from_resolution_error(exc) from exc

    links = identity_links.list_for_shared_part(ref.element_id)
    visible, hidden = _filter_by_document_view(
        links, "instance_document_id", _make_document_view_checker(current_user)
    )
    return {
        "ref": ref.to_dict(),
        "identity_links": [_identity_link_response(link) for link in visible],
        "hidden_count": hidden,
    }
