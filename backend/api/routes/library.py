"""分野別ナレッジライブラリ（L層）API — migration 042。

実パス: ``/api/admin/library/...``（TEACHER 以上）。設計の正本は
``docs/features/image_pipeline_knowledge_library_design.md`` §6・§7・§11。

- draft (`library_entries`) の作成・取得・一覧・楽観ロック更新・凍結・retire/restore は
  ``core.library.store`` を薄くラップするだけ（DB ロジックは core 側に集約、開発ルール2/7）。
- retrieval / 昇格モーダルの類似提示は ``core.library.search`` を薄くラップする。
- 例示画像（``exemplar_images``）は原則7（fail-closed）: 非空で渡された要素は必ず
  ``source_document_id`` の元 document 所有者チェックを通し、通過分のみサーバ側で
  ``approved_by`` / ``approved_at`` を付与して保存する（クライアント値は信用しない）。
- 行削除 API は無い（P4）。retire/restore の状態遷移のみ。
- 状態変更（作成・draft 更新・凍結・retire・restore・画像含有承認）はすべて
  ``theory_review_events``（``entity_type='library_entry'``）に監査記録する（既存の
  ``services.record_review_event`` を再利用。C層/D層/V層と同じ表・同じヘルパー）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

import services
from dependencies import _require_teacher

from core.schema import AUDIT_ENTITY_LIBRARY_ENTRY
from core.library import schema as library_schema
from core.library import search as library_search
from core.library import store as library_store
from core.library.store import LibraryConflictError, LibraryNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/library", tags=["Library"])


# ---------------------------------------------------------------------------
# Request models（開発ルール2: API固有モデルはこのファイル内に定義）
# ---------------------------------------------------------------------------


class ExemplarImageIn(BaseModel):
    """昇格リクエストに含まれる例示画像1件（承認前）。

    ``approved_by`` / ``approved_at`` はここに含めない（サーバ側で付与する。
    クライアントが送っても Pydantic により破棄されるため信用されない）。
    ``minio_key`` もクライアント値は信用せず、サーバ側で ``document_figures`` の
    実在行（figure_id × source_document_id）から解決する（provenance 検証を兼ねる）。
    """

    figure_id: str
    source_document_id: str
    minio_key: str | None = None  # 後方互換のため受理するが無視する（サーバ側で解決）


class LibraryEntryCreateRequest(BaseModel):
    domain_key: str
    entry_type: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    summary: str = ""
    body: dict[str, Any] = Field(default_factory=dict)
    source_component_ids: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    exemplar_images: list[ExemplarImageIn] = Field(default_factory=list)


class LibraryEntryUpdateRequest(BaseModel):
    """draft 編集（§6-3）。``expected_revision`` は楽観ロックのため必須。"""

    expected_revision: int
    name: str | None = None
    aliases: list[str] | None = None
    summary: str | None = None
    body: dict[str, Any] | None = None
    exemplar_images: list[ExemplarImageIn] | None = None
    source_component_ids: list[str] | None = None
    source_document_ids: list[str] | None = None


class FreezeRequest(BaseModel):
    note: str = ""


class SimilarEntriesRequest(BaseModel):
    domain_key: str
    text: str
    entry_type: str | None = None
    top_k: int = 5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid(current_user: dict) -> str | None:
    return current_user.get("id") or current_user.get("user_id")


def _audit(entity_type: str, entity_id: str, old_status: str, new_status: str, user_id: str | None, metadata: dict | None = None) -> None:
    """``theory_review_events`` への監査記録（best-effort。失敗しても操作自体は止めない）。"""
    services.record_review_event(entity_type, entity_id, old_status, new_status, user_id, metadata or {})


def _authorize_exemplar_images(images: list[ExemplarImageIn] | None, user_id: str | None) -> list[dict]:
    """例示画像の権利ゲート（原則7・fail-closed）。

    非空の ``images`` は各要素について:
    1. ``source_document_id`` の元 document をリクエストユーザーが**所有**していること
    2. ``figure_id`` がその document の ``document_figures`` に**実在**すること
       （provenance 検証 — 他人の document の図を自分の document 由来と偽装できない）
    を要求する。1件でも満たさなければ 403。``minio_key`` はクライアント値を使わず
    DB の実在行から解決し、``approved_by`` / ``approved_at`` をサーバ側で付与して返す。
    """
    if not images:
        return []
    from core.document_pipeline.figure_images import load_document_figures

    approved_at = datetime.now(timezone.utc).isoformat()
    figures_by_document: dict[str, dict[str, dict]] = {}
    authorized: list[dict] = []
    for image in images:
        if not image.source_document_id or not services.user_owns_document(user_id, image.source_document_id):
            raise HTTPException(
                status_code=403,
                detail="例示画像を含められるのは元教材の所有者のみです",
            )
        if image.source_document_id not in figures_by_document:
            figures_by_document[image.source_document_id] = {
                str(fig.get("id")): fig
                for fig in load_document_figures(image.source_document_id)
            }
        figure_row = figures_by_document[image.source_document_id].get(str(image.figure_id))
        if not figure_row or not figure_row.get("minio_key"):
            raise HTTPException(
                status_code=403,
                detail="指定された図はこの教材の抽出画像に存在しません",
            )
        authorized.append(
            {
                "figure_id": image.figure_id,
                "minio_key": figure_row["minio_key"],
                "source_document_id": image.source_document_id,
                "approved_by": user_id,
                "approved_at": approved_at,
            }
        )
    return authorized


def _get_entry_or_404(entry_id: str) -> dict:
    entry = library_store.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="library entry not found")
    return entry


# ---------------------------------------------------------------------------
# エントリ一覧・作成・取得・版履歴
# ---------------------------------------------------------------------------


@router.get("/entries")
def list_entries(
    domain_key: str | None = None,
    entry_type: str | None = None,
    q: str | None = None,
    include_retired: bool = False,
    current_user: dict = Depends(_require_teacher),
):
    try:
        entries = library_store.list_entries(
            domain_key=domain_key, entry_type=entry_type, q=q, include_retired=include_retired
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"entries": entries}


@router.post("/entries", status_code=201)
def create_entry(payload: LibraryEntryCreateRequest, current_user: dict = Depends(_require_teacher)):
    """draft エントリを新規作成する（昇格 / 手動作成の共通経路。§6-4）。"""
    uid = _uid(current_user)
    authorized_images = _authorize_exemplar_images(payload.exemplar_images, uid)

    try:
        entry = library_store.create_entry(
            domain_key=payload.domain_key,
            entry_type=payload.entry_type,
            name=payload.name,
            aliases=payload.aliases,
            summary=payload.summary,
            body=payload.body,
            exemplar_images=authorized_images,
            source_component_ids=payload.source_component_ids,
            source_document_ids=payload.source_document_ids,
            created_by=uid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _audit(
        AUDIT_ENTITY_LIBRARY_ENTRY,
        entry["id"],
        "",
        "created",
        uid,
        {
            "domain_key": entry["domain_key"],
            "entry_type": entry["entry_type"],
            "source_component_ids": entry["source_component_ids"],
            "source_document_ids": entry["source_document_ids"],
        },
    )
    if authorized_images:
        _audit(
            AUDIT_ENTITY_LIBRARY_ENTRY,
            entry["id"],
            "",
            "exemplar_images_approved",
            uid,
            {"domain_key": entry["domain_key"], "figure_ids": [img["figure_id"] for img in authorized_images]},
        )
    return entry


@router.get("/entries/{entry_id}")
def get_entry(entry_id: str, current_user: dict = Depends(_require_teacher)):
    return _get_entry_or_404(entry_id)


@router.get("/entries/{entry_id}/versions")
def list_versions(entry_id: str, current_user: dict = Depends(_require_teacher)):
    _get_entry_or_404(entry_id)
    return {"versions": library_store.list_versions(entry_id)}


# ---------------------------------------------------------------------------
# draft 更新（楽観ロック） / 凍結 / retire・restore
# ---------------------------------------------------------------------------


@router.put("/entries/{entry_id}")
def update_entry(entry_id: str, payload: LibraryEntryUpdateRequest, current_user: dict = Depends(_require_teacher)):
    uid = _uid(current_user)
    existing = _get_entry_or_404(entry_id)

    fields: dict[str, Any] = {}
    for key in ("name", "aliases", "summary", "body", "source_component_ids", "source_document_ids"):
        value = getattr(payload, key)
        if value is not None:
            fields[key] = value

    newly_added_figure_ids: list[str] = []
    if payload.exemplar_images is not None:
        authorized_images = _authorize_exemplar_images(payload.exemplar_images, uid)
        fields["exemplar_images"] = authorized_images
        existing_figure_ids = {img.get("figure_id") for img in existing.get("exemplar_images", [])}
        newly_added_figure_ids = [
            img["figure_id"] for img in authorized_images if img["figure_id"] not in existing_figure_ids
        ]

    if not fields:
        raise HTTPException(status_code=422, detail="更新するフィールドがありません")

    try:
        entry = library_store.update_entry(
            entry_id, expected_revision=payload.expected_revision, updated_by=uid, **fields
        )
    except LibraryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LibraryConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "他の教員が更新しました。再読み込みしてください",
                "current_revision": exc.current_revision,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _audit(
        AUDIT_ENTITY_LIBRARY_ENTRY,
        entry_id,
        "",
        "draft_updated",
        uid,
        {"domain_key": entry["domain_key"], "fields": sorted(fields.keys())},
    )
    if newly_added_figure_ids:
        _audit(
            AUDIT_ENTITY_LIBRARY_ENTRY,
            entry_id,
            "",
            "exemplar_images_approved",
            uid,
            {"domain_key": entry["domain_key"], "figure_ids": newly_added_figure_ids},
        )
    return entry


@router.post("/entries/{entry_id}/freeze")
def freeze_entry(
    entry_id: str,
    payload: FreezeRequest = Body(default=FreezeRequest()),
    current_user: dict = Depends(_require_teacher),
):
    uid = _uid(current_user)
    try:
        version = library_store.freeze_entry(entry_id, published_by=uid, note=payload.note)
    except LibraryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _audit(
        AUDIT_ENTITY_LIBRARY_ENTRY,
        entry_id,
        "",
        "frozen",
        uid,
        {"version_no": version["version_no"], "note": payload.note, "embedding_status": version["embedding_status"]},
    )
    return {
        "entry_id": entry_id,
        "version_no": version["version_no"],
        "embedding_status": version["embedding_status"],
    }


@router.post("/entries/{entry_id}/retire")
def retire_entry(entry_id: str, current_user: dict = Depends(_require_teacher)):
    uid = _uid(current_user)
    try:
        entry = library_store.retire_entry(entry_id, updated_by=uid)
    except LibraryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _audit(AUDIT_ENTITY_LIBRARY_ENTRY, entry_id, library_schema.STATUS_ACTIVE, library_schema.STATUS_RETIRED, uid, {"domain_key": entry["domain_key"]})
    return entry


@router.post("/entries/{entry_id}/restore")
def restore_entry(entry_id: str, current_user: dict = Depends(_require_teacher)):
    uid = _uid(current_user)
    try:
        entry = library_store.restore_entry(entry_id, updated_by=uid)
    except LibraryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _audit(AUDIT_ENTITY_LIBRARY_ENTRY, entry_id, library_schema.STATUS_RETIRED, library_schema.STATUS_ACTIVE, uid, {"domain_key": entry["domain_key"]})
    return entry


# ---------------------------------------------------------------------------
# domain サマリ / 類似検索（昇格モーダル・retrieval 動作確認用）
# ---------------------------------------------------------------------------


@router.get("/domains")
def domain_summary(current_user: dict = Depends(_require_teacher)):
    return {"domains": library_store.domain_summary()}


@router.post("/entries/similar")
def find_similar_entries(payload: SimilarEntriesRequest, current_user: dict = Depends(_require_teacher)):
    entries = library_search.find_similar_entries(
        domain_key=payload.domain_key,
        text=payload.text,
        entry_type=payload.entry_type,
        top_k=payload.top_k,
    )
    return {"entries": entries}
