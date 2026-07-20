"""W層（要素検討ワークスペース）API（実パス ``/api/admin/deliberation/...``）。

設計書 `docs/features/element_deliberation_workspace_design.md` §8。全経路 ``_require_teacher``。
document-scoped 要素は ``_ensure_document_viewable``/``_ensure_document_editable`` で
fail-closed（W5）。

Phase 0（overview）: 面①「内訳」+ 面②「位置づけ」（§4.1/4.3/4.4）の集約 overview。

Phase 1（本増分）: 面②に §4.2 コーパス横断レンズ（chunk-proxy 方式）を追加。
`core.deliberation.positioning.build()` が embedding 検索で近傍 chunk を引き、
その material_id から他 document の候補（各 item に `document_id` 付き）を返す。
core は per-user 権限を判定できないため、閲覧不可 document 由来の候補は本ルータの
`_apply_cross_corpus_gate` が最終フィルタする（W5・fail-closed）。

Phase W-β: 同一性リンク（`element_identity_links`、migration 048）の
作成・確定・却下・一覧。設計は `docs/features/knowledge_network_vision.md`
（§3 修正② / §4 KN-2・KN-3）と本設計書 §5.5。LLM 呼び出しはゼロ（人間操作のみ）。
確定・却下は常に候補（`status='candidate'`）からの人間の判断（KN-3）で、
行削除 API は作らない（W4）。

Phase 2（本増分、migration 049）: 対話的検討 + 候補注釈 + コミットルーティング（§5）。
`deliberation_sessions` / `element_annotations` の CRUD は `core.deliberation.store`
（生SQL・FastAPI 非import）、grounding 構築 + LLM 1ターン実行は
`core.deliberation.dialogue`（`core.llm.generate_conversation_turn` 経由。会話は
教員起動の同期だが 1 応答=1 LLM コール・コスト上限あり、W6/§11）、候補注釈の検証
（W3: evidence/reason 必須）・status 遷移・コミットルーティング（§5 表・3経路のみ）は
`core.deliberation.annotations` が担う。生成された候補注釈は常に `status='candidate'`
（W2）。commit は `_ensure_document_editable`（domain-scoped は `_require_teacher`
のみ）。positioning_note / standardization の commit は v1 未対応（422、dismiss のみ可）。

Phase S（本増分、migration 050）: 標準化判定 worker（知識ネットワークビジョン §3 修正③・
§6・§7）の手動バッチ起動 API。三角測量（①LLM 事前知識 ②L層凍結版類似 ③コーパス内反復）は
`core.deliberation.standardization`（llm_worker 6系統目アダプタ）が非同期 daemon thread
（`core/tension/worker.py` と同型）で実行し、常に `status='candidate'` の
`element_annotations`（`kind='standardization'`）を書く。本ルータはトリガーのみで
LLM 呼び出しを含む実処理は行わない（W6 同様に同期パスを重くしない）。確定（commit）は
既存の `POST /annotations/{id}/commit` がそのまま扱う（§5 表に `standardization` →
`library_entries.standardization_status` 経路を追加済み）。

要素インベントリ（本増分、`docs/features/element_inventory_design.md`）: 教材（document）
単位で `theory_component` / `theory_claim` / `equation` / `figure` の全検出要素を統一カード
一覧で返す `GET /documents/{document_id}/elements`。W層「深く検討」の文脈的導線
（figure/equation/theory_component/theory_claim がそれぞれ別画面に埋め込まれている）を
補う俯瞰・探索用の第5導線で、新しい層ではない。全要素が同一 document に属するため
per-element ゲートは不要（`services.resolve_document_access` で正規化 + 閲覧判定を1回だけ
行う。fail-closed=404）。組み立ては非LLM・DB 書き込みゼロの `core.deliberation.inventory.build()`
に一任し、本ルータは権限ゲートと整形のみを担う。

core 側（`core.deliberation`）は FastAPI を import しない。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from dependencies import _require_teacher
from core.config import get_settings
from core.deliberation import (
    annotations as delib_annotations,
    context_lens,
    decomposition,
    dialogue,
    identity_links,
    inventory,
    positioning,
    refs,
    store as delib_store,
)
from core.library import search as library_search
from core.library import store as library_store
from core.deliberation.standardization import worker as standardization_worker
from core.llm_worker.history import window_history
from core.deliberation.schema import (
    ELEMENT_FIGURE,
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
    """同一性リンク行を API 応答用に整形する（W8: confidence は生値を返さずラベルのみ）。

    脱UUID（`hierarchical_context_explanation_design.md` §6）: ``shared_part_id`` が
    指す L層エントリの name・summary 冒頭を同梱し、UI が生 UUID の代わりに読める
    名前を表示できるようにする（deliberation.js の `_identityLinkRowHtml`）。
    エントリが見つからない・読み取りに失敗した場合はキー自体を省略する
    （fail-soft。UUID 自体は既存どおり ``shared_part_id`` に残るため情報は失われない）。
    """
    response = dict(link)
    raw_confidence = response.pop("confidence", None)
    response["confidence_label"] = identity_links.confidence_label(raw_confidence)
    shared_part_id = str(link.get("shared_part_id") or "").strip()
    if shared_part_id:
        try:
            entry = library_store.get_entry(shared_part_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "deliberation: library entry lookup failed for shared_part_id=%s",
                shared_part_id, exc_info=True,
            )
            entry = None
        if entry:
            response["shared_part_name"] = entry.get("name") or ""
            response["shared_part_summary"] = str(entry.get("summary") or "")[:160]
    return response


def _element_explanation_response(row: dict[str, Any]) -> dict[str, Any]:
    """element_explanations の1行を overview 応答用に整形する（E6: confidence は
    生値を返さずラベルのみ。承認 API（``routes/element_explanations.py`` の
    ``_public_row``）と同じ変換方針を overview 応答でも独立に適用する）。
    """
    evidence = dict(row.get("evidence") or {})
    raw_confidence = evidence.pop("confidence", None)
    evidence["confidence_label"] = identity_links.confidence_label(raw_confidence)
    return {
        "id": row.get("id"),
        "kind": row.get("kind"),
        "body": row.get("body"),
        "evidence": evidence,
        "status": row.get("status"),
        "created_by": row.get("created_by"),
        "reviewed_by": row.get("reviewed_by"),
        "reviewed_at": row.get("reviewed_at"),
        "created_at": row.get("created_at"),
    }


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


def _apply_linked_instances_gate(breakdown: dict[str, Any], current_user: dict) -> None:
    """shared_part の面①内訳（``fields.linked_instances``、設計書 §6 逆方向）を、
    各インスタンスの由来 document の閲覧権限でフィルタする（W5: fail-closed）。

    ``core.deliberation.decomposition._decompose_shared_part`` は per-user 権限を
    判定できないため、confirmed な同一性リンクを持つインスタンス全件を返す
    （``_confirmed_linked_instances``）。ここで閲覧不可 document 由来のインスタンスを
    除外し、隠した件数を ``fields.linked_instances_hidden_count`` として正直に返す
    （``_apply_exemplar_image_gate`` と同型のパターン。P4・出所の正直さ）。
    """
    fields = breakdown.get("fields")
    if not isinstance(fields, dict):
        return
    instances = fields.get("linked_instances")
    if not isinstance(instances, list) or not instances:
        return
    visible, hidden = _filter_by_document_view(
        instances, "document_id", _make_document_view_checker(current_user)
    )
    fields["linked_instances"] = visible
    fields["linked_instances_hidden_count"] = hidden


def _apply_cross_corpus_gate(positioning_payload: dict[str, Any], current_user: dict) -> None:
    """面② cross_corpus レンズ（設計書 §4.2、Phase 1）の items を、各 item の由来
    document 閲覧権限でフィルタする（fail-closed・W5）。

    ``core.deliberation.positioning`` は per-user 権限を判定できない（core は
    services/routes を import しない）ため、``_build_cross_corpus`` が返す items
    （各 item に ``document_id`` フィールドを持つ）をこの route 層で最終フィルタする。
    隠した件数は ``hidden_count`` として正直に返す（P4・出所の正直さ。ただし UI は
    描画しない）。フィルタ後 items が空ならレンズ自体を None に落とす
    （``_apply_exemplar_image_gate`` と同型のパターン）。
    """
    lenses = positioning_payload.get("lenses")
    if not isinstance(lenses, dict):
        return
    lens = lenses.get("cross_corpus")
    if not isinstance(lens, dict):
        return
    items = lens.get("items")
    if not isinstance(items, list) or not items:
        return
    visible, hidden = _filter_by_document_view(
        items, "document_id", _make_document_view_checker(current_user)
    )
    if not visible:
        lenses["cross_corpus"] = None
        return
    lens["items"] = visible
    lens["hidden_count"] = hidden


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
    - 数値（confidence 等）は返さない（W8）。
    - 面②の cross_corpus レンズ（§4.2、Phase 1）は他 document の候補を返しうるため、
      閲覧不可 document 由来の候補をこの route 層でフィルタする（``_apply_cross_corpus_gate``）。
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
        # 設計書 §6 shared_part 逆方向: confirmed な同一性リンクを持つインスタンス一覧
        # （fields.linked_instances）も同じ理由で由来 document の閲覧権限を継承する。
        _apply_linked_instances_gate(breakdown, current_user)

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

    if positioning_payload.get("available"):
        # cross_corpus は他 document の候補を返しうるため、閲覧不可 document 由来の
        # 候補をここでフィルタする（W5・fail-closed。§4.2「閲覧不可のものは除外」）。
        _apply_cross_corpus_gate(positioning_payload, current_user)

    # 面③ 要素中心コンテキストビュー（Issue #498、`context_lens.py`）。document-scoped
    # 4要素型のみ投影を持ち、shared_part（scope='domain'）は対象外（None）。positioning と
    # 同様に独立した try/except にし、この面の失敗が decomposition/positioning の結果を
    # 握りつぶさない（fail-soft・W6）。既存 overview 経路の権限ゲート（上の
    # ``_ensure_document_viewable``）がそのまま効くため、追加のゲートは要らない
    # （論文内に閉じた投影のため cross_corpus のような他 document 由来の候補も持たない）。
    try:
        context_result = context_lens.build(ref)
        if context_result is None:
            context_payload: dict[str, Any] = {
                "available": False,
                "note": "この要素型にはコンテキスト投影がありません",
            }
        else:
            context_payload = {"available": True, **context_result}
    except Exception:  # noqa: BLE001
        logger.warning(
            "deliberation context lens failed for %s:%s", ref.element_type, ref.element_id, exc_info=True
        )
        context_payload = {
            "available": False,
            "note": "コンテキスト投影の取得に失敗しました",
        }

    # 説明（Phase 2, migration 056。`hierarchical_context_explanation_design.md` §5.2/§5.3）。
    # focus 要素の element_explanations（candidate + approved のみ。dismissed/superseded は
    # 除外）を同梱する。document スコープ要素のみ対象（element_explanations.element_type の
    # 語彙に shared_part は無い）。context/positioning と同様、独立した try/except にする
    # （fail-soft・この面の失敗が他の面の結果を握りつぶさない）。
    try:
        if ref.scope == SCOPE_DOCUMENT:
            explanation_rows = decomposition.explanations_for_element(ref)
            explanations_payload: dict[str, Any] = {
                "available": True,
                "items": [_element_explanation_response(r) for r in explanation_rows],
            }
        else:
            explanations_payload = {
                "available": False,
                "note": "この要素型には説明がありません",
            }
    except Exception:  # noqa: BLE001
        logger.warning(
            "deliberation element explanations failed for %s:%s", ref.element_type, ref.element_id, exc_info=True
        )
        explanations_payload = {
            "available": False,
            "note": "説明の取得に失敗しました",
        }

    return {
        "ref": ref.to_dict(),
        "decomposition": breakdown,
        "positioning": positioning_payload,
        "context": context_payload,
        "explanations": explanations_payload,
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


@router.get("/elements/{element_type}/{element_id}/shared-part-candidates")
def list_shared_part_candidates_for_element(
    element_type: str,
    element_id: str,
    document_id: str | None = Query(
        default=None,
        description="equation 要素で必須。他型では無視される。",
    ),
    q: str = Query(
        default="",
        description="検索テキスト。空なら対象要素の内訳（label・text 等）から自動で組み立てる。",
    ),
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """手動の同一性リンク作成用: 同分野の類似 library_entries 候補を返す（N2）。

    - 非LLM・DB 非変更（読み取りのみ）。検索は L層の既存 ``find_similar_entries``
      （embedding + ILIKE マージ。draft のみの active エントリも ILIKE 側で拾える —
      同一性リンクは ``library_entries.id`` を参照するため凍結の有無を問わない）の再利用。
    - ``domain_key`` は対象要素の document → 最新解析 run の cartridge_id からサーバ側で
      決定論的に解決する（``dialogue.document_domain_key``。フロントに domain 知識を
      持たせない）。解決できない場合は 0 件 + 事実文 note で縮退する（fail-soft）。
    - 対象は document-scoped インスタンスのみ（identity link の source 制約と同じ）。
      閲覧ゲートは ``_ensure_document_viewable``（検索は読み取り・作成時は
      ``POST /identity-links`` 側の ``_ensure_document_editable`` が別途ゲートする）。
    - 数値（distance 等）は返さない（W8）。
    """
    try:
        ref = refs.resolve(element_type, element_id, document_id=document_id)
    except ElementResolutionError as exc:
        raise _http_from_resolution_error(exc) from exc
    if ref.scope != SCOPE_DOCUMENT:
        raise HTTPException(
            status_code=422,
            detail="identity link source must be a document-scoped instance element",
        )
    _ensure_document_viewable(ref.document_id or "", current_user)

    domain_key = dialogue.document_domain_key(ref.document_id or "")
    if not domain_key:
        return {
            "domain_key": "",
            "entries": [],
            "note": "この教材の分野（カートリッジ）が特定できないため、共通部品を検索できません。",
        }

    query_text = (q or "").strip()
    if not query_text:
        try:
            query_text = dialogue.identity_query_text(decomposition.build(ref))
        except ElementResolutionError as exc:
            raise _http_from_resolution_error(exc) from exc
    if not query_text:
        return {
            "domain_key": domain_key,
            "entries": [],
            "note": "検索テキストを入力してください。",
        }

    settings = get_settings()
    top_k = int(getattr(settings, "deliberation_identity_candidates_top_k", 5))
    hits = library_search.find_similar_entries(
        domain_key=domain_key,
        text=query_text,
        entry_type=dialogue.identity_entry_type_for_element(ref.element_type),
        top_k=top_k,
    )
    entries = [
        {
            "shared_part_id": str(hit.get("entry_id") or ""),
            "name": str(hit.get("name") or ""),
            "aliases": [str(a) for a in (hit.get("aliases") or []) if str(a or "").strip()],
            "summary": str(hit.get("summary") or ""),
        }
        for hit in hits
        if hit.get("entry_id")
    ]
    response: dict[str, Any] = {"domain_key": domain_key, "entries": entries}
    if not entries:
        response["note"] = "同分野の共通部品が見つかりません。"
    return response


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


# ---------------------------------------------------------------------------
# Phase 2: 対話的検討 + 候補注釈 + コミットルーティング
# ---------------------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    """対話セッション開始リクエスト（設計書 §8）。"""

    scope: str
    element_type: str
    element_id: str
    document_id: str | None = Field(
        default=None,
        description="equation 要素で必須（refs.resolve の一意化に使う）。他型では無視される。",
    )
    title: str = ""


class SelectedFigureContext(BaseModel):
    """Bounded UI focus hint; never treated as evidence or hidden authority."""

    kind: Literal["part", "observation", "subject"]
    id: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=300)


class MessageCreateRequest(BaseModel):
    content: str
    selected_context: SelectedFigureContext | None = None


def _session_response(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": session["id"],
        "scope": session["scope"],
        "element_type": session["element_type"],
        "element_id": session["element_id"],
        "document_id": session.get("document_id"),
        "domain_key": session.get("domain_key"),
        "title": session.get("title", ""),
        "messages": session.get("messages") or [],
        "created_at": session.get("created_at", ""),
    }


def _annotation_response(annotation: dict[str, Any]) -> dict[str, Any]:
    """候補/確定/却下注釈を API 応答用に整形する（W8: confidence は生値を返さずラベルのみ。
    ``identity_links.confidence_label`` を再利用し、W層内で閾値の定義を二重化しない）。
    """
    commit_supported, commit_note = delib_annotations.commit_capability(annotation)
    return {
        "id": annotation["id"],
        "kind": annotation["kind"],
        "body": annotation.get("body") or {},
        "evidence": annotation.get("evidence") or [],
        "reason": annotation.get("reason", ""),
        "confidence_label": identity_links.confidence_label(annotation.get("confidence")),
        "status": annotation["status"],
        "committed_target": annotation.get("committed_target") or {},
        "created_at": annotation.get("created_at", ""),
        "commit_supported": commit_supported,
        "commit_note": commit_note,
    }


def _http_from_commit_error(exc: delib_annotations.CommitRoutingError) -> HTTPException:
    status = {"not_found": 404, "conflict": 409}.get(exc.kind, 422)
    return HTTPException(status_code=status, detail=str(exc))


def _ensure_session_owner(session: dict[str, Any] | None, current_user: dict) -> dict[str, Any]:
    """セッションの作成者本人のみアクセスできる（他人のセッションは404。設計書 §8）。"""
    if not session or session.get("created_by") != current_user.get("id"):
        raise HTTPException(status_code=404, detail="deliberation session not found")
    return session


@router.post("/sessions")
def create_deliberation_session(
    body: SessionCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """対話セッションを開始する（設計書 §8）。

    ゲート: document-scoped 要素は ``_ensure_document_viewable``（fail-closed・W5）。
    domain-scoped（shared_part）は L層方針により ``_require_teacher`` のみ。
    """
    try:
        ref = refs.resolve(body.element_type, body.element_id, document_id=body.document_id)
    except ElementResolutionError as exc:
        raise _http_from_resolution_error(exc) from exc
    if ref.scope != body.scope:
        raise HTTPException(
            status_code=422,
            detail=f"scope {body.scope!r} does not match the resolved element (scope={ref.scope!r})",
        )
    if ref.scope == SCOPE_DOCUMENT:
        _ensure_document_viewable(ref.document_id or "", current_user)

    session = delib_store.create_session(ref, title=body.title, created_by=current_user.get("id"))
    record_review_event(
        AUDIT_ENTITY_DELIBERATION,
        session["id"],
        "",
        "session_started",
        current_user.get("id"),
        {"action": "session.create", "element_type": ref.element_type, "element_id": ref.element_id},
    )
    return {"session": _session_response(session)}


@router.get("/sessions/{session_id}")
def get_deliberation_session(
    session_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """セッション取得（messages 込み）。作成者本人のみ（他人のセッションは404）。"""
    session = _ensure_session_owner(delib_store.get_session_by_id(session_id), current_user)
    return {"session": _session_response(session)}


@router.post("/sessions/{session_id}/messages")
def post_deliberation_message(
    session_id: str,
    body: MessageCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """1ターン送信 → 応答 + 候補注釈（1 LLM コール・W6）。

    grounding（面①内訳 + 面②レンズ要約）は最初の user メッセージにのみ注入する
    （設計書 §5）。cross_corpus / exemplar_images の権限フィルタは overview と同じ
    route 層ゲートを適用してから grounding テキスト化する（W5）。figure 要素は
    vision（画像添付）で対話する。生成された候補注釈は常に ``status='candidate'``
    で永続化する（W2）。
    """
    session = _ensure_session_owner(delib_store.get_session_by_id(session_id), current_user)

    try:
        ref = refs.resolve(
            session["element_type"], session["element_id"], document_id=session.get("document_id")
        )
    except ElementResolutionError as exc:
        raise _http_from_resolution_error(exc) from exc
    if ref.scope == SCOPE_DOCUMENT:
        _ensure_document_viewable(ref.document_id or "", current_user)

    user_content = (body.content or "").strip()
    if not user_content:
        raise HTTPException(status_code=422, detail="content is required")

    if not dialogue.check_and_count_llm_call(session_id, current_user.get("id")):
        raise HTTPException(
            status_code=429,
            detail="本日またはこのセッションでの対話回数の上限に達しました。しばらくしてから再度お試しください。",
        )

    grounding = dialogue.build_grounding(ref)
    if grounding.get("positioning", {}).get("available"):
        _apply_cross_corpus_gate(grounding["positioning"], current_user)
    if ref.element_type == ELEMENT_SHARED_PART:
        _apply_exemplar_image_gate(grounding["breakdown"], current_user)
    grounding_text = dialogue.grounding_to_text(grounding)
    llm_user_content = user_content
    if body.selected_context is not None:
        context = body.selected_context
        # This text is an explicit focus hint selected from the visible UI.
        # It is not persisted in the message and must not be mistaken for
        # source evidence or a teacher-confirmed statement.
        llm_user_content = (
            "[User-selected UI focus; use only to scope the question, not as evidence] "
            f"kind={context.kind}; id={context.id}; label={context.label}\n\n"
            + user_content
        )

    images: list[bytes] | None = None
    if ref.element_type == ELEMENT_FIGURE:
        image_bytes = dialogue.figure_image_bytes(ref.element_id)
        images = [image_bytes] if image_bytes else None

    prior_messages = [
        {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")}
        for m in (session.get("messages") or [])
        if isinstance(m, dict)
    ]
    # 会話履歴のウィンドウ化（設計書 §2-2）。grounding は dialogue.build_llm_messages が
    # prior_messages + 今回発話のうち「最初に見つかった user メッセージ」に注入する
    # （dialogue.py:548-568）。prior_messages[0] は常にセッション最初のユーザー発話
    # （grounding が乗る場所）なので head_keep=1 でそれを保護する。セッション8コール
    # 上限下では実質 no-op（安全網）。
    prior_messages = window_history(prior_messages, max_messages=16, max_chars=4000, head_keep=1)

    result = dialogue.run_turn(
        ref,
        prior_messages=prior_messages,
        user_content=llm_user_content,
        grounding_text=grounding_text,
        images=images,
        user_id=current_user.get("id"),
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    delib_store.append_messages(
        session_id,
        [
            {"role": "user", "content": user_content, "created_at": now_iso},
            {"role": "assistant", "content": result.reply, "created_at": now_iso},
        ],
    )

    created_annotations = delib_annotations.create_candidates_from_dialogue(
        ref,
        session_id=session_id,
        raw_items=result.annotations,
        created_by=current_user.get("id"),
    )
    for annotation in created_annotations:
        record_review_event(
            AUDIT_ENTITY_DELIBERATION,
            annotation["id"],
            "",
            annotation["status"],
            current_user.get("id"),
            {"action": "annotation.candidate_generated", "kind": annotation["kind"], "session_id": session_id},
        )

    return {
        "reply": result.reply,
        "annotations": [_annotation_response(a) for a in created_annotations],
        "degraded": result.degraded,
    }


@router.get("/elements/{element_type}/{element_id}/annotations")
def list_element_annotations(
    element_type: str,
    element_id: str,
    document_id: str | None = Query(
        default=None,
        description="equation 要素で必須（独立テーブルを持たないため document で一意化)。"
        "他型では無視される。",
    ),
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """要素に付いた候補/確定/却下注釈の一覧（confidence はラベル・W8）。"""
    try:
        ref = refs.resolve(element_type, element_id, document_id=document_id)
    except ElementResolutionError as exc:
        raise _http_from_resolution_error(exc) from exc
    if ref.scope == SCOPE_DOCUMENT:
        _ensure_document_viewable(ref.document_id or "", current_user)
    # domain-scoped（shared_part）は L層方針で _require_teacher のみ（overview と同型）。

    items = delib_store.list_annotations_for_element(
        ref.element_type, ref.element_id, document_id=ref.document_id, domain_key=ref.domain_key,
    )
    return {"annotations": [_annotation_response(a) for a in items]}


@router.post("/annotations/{annotation_id}/commit")
def commit_element_annotation(
    annotation_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """候補注釈を確定し、既存構造へルーティングする（``_ensure_document_editable``）。

    theory_component の meaning/decomposition、figure の構造化 decomposition、
    interpretation、identity、standardization を各保存先へ反映する。UI は
    ``commit_capability`` が false の候補には確定操作を提示しない。
    """
    existing = delib_store.get_annotation(annotation_id)
    if not existing:
        raise HTTPException(status_code=404, detail="annotation not found")
    if existing["scope"] == SCOPE_DOCUMENT:
        _ensure_document_editable(existing.get("document_id") or "", current_user)
    # domain-scoped（shared_part）は L層方針で _require_teacher のみ。

    try:
        updated = delib_annotations.commit(annotation_id, current_user_id=current_user.get("id"))
    except delib_annotations.CommitRoutingError as exc:
        raise _http_from_commit_error(exc) from exc

    record_review_event(
        AUDIT_ENTITY_DELIBERATION,
        annotation_id,
        existing["status"],
        updated["status"],
        current_user.get("id"),
        {"action": "annotation.commit", "kind": existing["kind"]},
    )
    return {
        "annotation": _annotation_response(updated),
        "committed_target": updated.get("committed_target") or {},
    }


@router.post("/annotations/{annotation_id}/dismiss")
def dismiss_element_annotation(
    annotation_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """候補注釈を却下する（status 遷移で保持・W4。``_ensure_document_editable``）。"""
    existing = delib_store.get_annotation(annotation_id)
    if not existing:
        raise HTTPException(status_code=404, detail="annotation not found")
    if existing["scope"] == SCOPE_DOCUMENT:
        _ensure_document_editable(existing.get("document_id") or "", current_user)

    updated = delib_annotations.dismiss(annotation_id, updated_by=current_user.get("id"))
    if updated is None:
        raise HTTPException(status_code=409, detail="annotation is not a candidate (already decided)")

    record_review_event(
        AUDIT_ENTITY_DELIBERATION,
        annotation_id,
        existing["status"],
        updated["status"],
        current_user.get("id"),
        {"action": "annotation.dismiss", "kind": existing["kind"]},
    )
    return {"annotation": _annotation_response(updated)}


# ---------------------------------------------------------------------------
# Phase S: 標準化判定 worker の手動バッチ起動（知識ネットワークビジョン §6・§7）
# ---------------------------------------------------------------------------
#
# domain-scoped（shared_part）なので L層方針で ``_require_teacher`` のみ（overview /
# identity-links の shared_part 系エンドポイントと同型）。LLM 呼び出し・retrieval を
# 含む実処理は同期処理せず、既存の tension/doubt.scope_candidates と同型の daemon
# thread へ委譲する（結果は element_annotations の candidate として非同期に書かれ、
# 教員は GET .../annotations で後から確認する）。冪等性チェック（既に candidate/committed
# の standardization 注釈があるか）だけは軽い DB 読み取りのため同期で行い、件数の生数字を
# 出さない事実文で応答する（W8）。


@router.post("/shared-parts/{shared_part_id}/standardization/assess")
def assess_shared_part_standardization(
    shared_part_id: str,
    force: bool = Query(
        default=False,
        description="既に candidate/committed の standardization 注釈があっても再評価する。",
    ),
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """1つの共通部品（shared_part）について標準化判定（三角測量）の評価を開始する。"""
    try:
        ref = refs.resolve(ELEMENT_SHARED_PART, shared_part_id)
    except ElementResolutionError as exc:
        raise _http_from_resolution_error(exc) from exc

    if not force and standardization_worker.has_pending_or_committed_assessment(
        ref.element_id, ref.domain_key or ""
    ):
        return {
            "queued": False,
            "note": "既に標準化判定の候補または確定があるため、評価をスキップしました"
            "（force で再評価できます）。",
        }

    standardization_worker.schedule_assess_shared_part(ref.element_id, force=force)
    record_review_event(
        AUDIT_ENTITY_DELIBERATION,
        ref.element_id,
        "",
        "standardization_assess_requested",
        current_user.get("id"),
        {"action": "standardization.assess_requested", "scope": "shared_part"},
    )
    return {"queued": True, "note": "標準化判定の評価をバックグラウンドで開始しました。"}


@router.post("/domains/{domain_key}/standardization/assess")
def assess_domain_standardization(
    domain_key: str,
    force: bool = Query(
        default=False,
        description="既に candidate/committed の standardization 注釈があるエントリも再評価する。",
    ),
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """domain 内の active な共通部品すべてについて標準化判定の評価を開始する。

    件数の生数字は返さない（W8）——対象がいくつあるかは
    ``GET /elements/shared_part/{id}/annotations`` を個別に確認する。
    """
    standardization_worker.schedule_assess_domain(domain_key, force=force)
    record_review_event(
        AUDIT_ENTITY_DELIBERATION,
        domain_key,
        "",
        "standardization_assess_requested",
        current_user.get("id"),
        {"action": "standardization.assess_requested", "scope": "domain"},
    )
    return {
        "queued": True,
        "note": "この分野の標準化判定の評価をバックグラウンドで開始しました"
        "（既に候補・確定があるエントリはスキップされます）。",
    }


# ---------------------------------------------------------------------------
# 要素インベントリ（`docs/features/element_inventory_design.md` §5）
# ---------------------------------------------------------------------------
#
# 教材（document）単位で theory_component / theory_claim / equation / figure の
# 全検出要素を統一カード一覧で返す、教材管理から開く俯瞰・探索用の第5導線（§1/§2）。
# フィルタは全面クライアントサイド（§6）なのでクエリパラメータは無い。
# 全要素が同一 document に属するため per-element ゲートは不要で、
# `services.resolve_document_access` による document 単位の正規化 + 閲覧判定を1回だけ
# 行う（§5/I2）。不在・非閲覧は 404（既存 `_ensure_document_viewable` と同じ fail-closed
# の挙動に合わせる — 403 は使わない）。組み立て自体は `core.deliberation.inventory.build()`
# に一任し（非LLM・DB 書き込みゼロ・I1）、本ルータは権限ゲートと正規化済み document_id の
# 受け渡しだけを行う。


@router.get("/documents/{document_id}/elements")
def get_document_element_inventory(
    document_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict[str, Any]:
    """document 1件分の検出要素インベントリを返す（設計書 §5）。

    ``document_id`` は ``documents.id``(UUID) と ``source_path``(material_id) の
    どちらでも受け付ける（原稿スタジオはコースの sources の material_id をそのまま渡す）。
    ``resolve_document_access`` で正規化した ``access.document_id`` を
    ``inventory.build()`` に渡す（正規化前の値のまま渡すと各テーブルの document_id
    照合が空振りする）。
    """
    access = resolve_document_access(current_user.get("id"), document_id)
    if not access.found or not access.can_view:
        raise HTTPException(status_code=404, detail="Document not found")
    return inventory.build(access.document_id)
