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
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError

import services
from dependencies import _require_teacher

from core.schema import AUDIT_ENTITY_LIBRARY_ENTRY
from core.deliberation import identity_links as _identity_links
from core import atlas_correspondence
from core import atlas_store
from core.library import atlas_links as library_atlas_links
from core.library import registry as library_registry
from core.library import schema as library_schema
from core.library import search as library_search
from core.library import store as library_store
from core.library.store import LibraryConflictError, LibraryNotFoundError, LibraryRetiredError
from core.postgres import get_session
from core.status import cross_layer_notify

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


# -- 概念レジストリ（Phase 3 / migration 082。concept_registry_design.md §9）-----------


class EntryReviewRequest(BaseModel):
    """概念（候補）の確定 / 見送り / 差し戻し。``dismissed`` は理由必須（KR7）。"""

    status: str
    review_note: str = ""


class LabelCreateRequest(BaseModel):
    """別名（alternate）・隠しラベル（hidden）の追加。手動追加は manual_curation。"""

    kind: str
    label: str
    language: str = ""


class LabelDismissRequest(BaseModel):
    review_note: str = ""


class RelationCreateRequest(BaseModel):
    """概念どうしの関係候補の手動作成（KR3: リンクであってマージではない）。"""

    subject_entry_id: str
    object_entry_id: str
    kind: str
    reason: str = ""


class DecideRequest(BaseModel):
    """関係 / node リンクの判断（``confirmed`` / ``dismissed`` / ``candidate``）。"""

    status: str
    review_note: str = ""


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
    include_candidates: bool = False,
    current_user: dict = Depends(_require_teacher),
):
    """エントリ一覧。

    既定は確定済み（``review_status='confirmed'``）のみで後方互換。
    ``include_candidates=true`` で AI が立てた候補・見送り済みも返す（§4.2）。
    """
    try:
        entries = library_store.list_entries(
            domain_key=domain_key,
            entry_type=entry_type,
            q=q,
            include_retired=include_retired,
            include_candidates=include_candidates,
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
    except LibraryRetiredError as exc:
        # N29: retired は読み取り専用（編集には restore が先）。事実文をそのまま返す。
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    except LibraryRetiredError as exc:
        # N29: retired は読み取り専用（凍結には restore が先）。事実文をそのまま返す。
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LibraryConflictError as exc:
        # KR2: 未確定（candidate）・見送り（dismissed）の概念は凍結できない。凍結版は
        # パイプライン retrieval と学習者に届く面なので、ここが教員確定の弁になる。
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        # UNIQUE(entry_id, version_no) 競合 = 同時に別の凍結が同じ次版番号を確保した。
        # 素の 500 で漏らさず、draft 更新の楽観ロック衝突（409）と同じ流儀で返す。
        raise HTTPException(
            status_code=409,
            detail="同時に凍結が実行されました。再読み込みして再試行してください",
        ) from exc

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


def _notify_citers_of_retirement(entry_id: str, domain_key: str, actor_id: str | None) -> None:
    """引用者（＝当該エントリに confirmed の同一性リンクを張った instance document の
    所有者たち）へ retire を通知する（横断インボックス fan-out, N14, best-effort）。

    「引用者」の導出: W-β の `element_identity_links`（migration 048）で
    `shared_part_id=entry_id` かつ `status='confirmed'` の行を集め、各行の
    `instance_document_id`（インスタンス側 document）の所有者を宛先とする
    （候補・却下は対象にしない — KN-3「未確定の同一視を事実として辿らせない」と同じ
    fail-closed 原則）。retire を実行した教員本人は宛先から除外する。
    """
    try:
        links = _identity_links.list_for_shared_part(entry_id)
    except Exception:  # noqa: BLE001 — 宛先解決の失敗は retire を止めない
        logger.debug("cross-layer notify (library retire): identity link lookup failed for %s", entry_id, exc_info=True)
        return
    doc_ids = {
        str(link.get("instance_document_id") or "").strip()
        for link in links
        if link.get("status") == "confirmed" and link.get("instance_document_id")
    }
    if not doc_ids:
        return
    actor = str(actor_id or "")
    recipients: set[str] = set()
    for doc_id in doc_ids:
        try:
            doc = services._resolve_document(doc_id)
        except Exception:  # noqa: BLE001
            continue
        owner_id = str(doc.get("uploaded_by") or "") if doc else ""
        if owner_id and owner_id != actor:
            recipients.add(owner_id)
    for recipient_id in recipients:
        try:
            cross_layer_notify.notify_user(
                recipient_id,
                cross_layer_notify.NOTIF_LIBRARY_ENTRY_RETIRED,
                "library_entry",
                entry_id,
                {"domain_key": domain_key},
            )
        except Exception:  # noqa: BLE001
            logger.debug("cross-layer notify (library retire) skipped for %s -> %s", entry_id, recipient_id, exc_info=True)


@router.post("/entries/{entry_id}/retire")
def retire_entry(entry_id: str, current_user: dict = Depends(_require_teacher)):
    uid = _uid(current_user)
    try:
        entry, previous_status = library_store.retire_entry(entry_id, updated_by=uid)
    except LibraryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # old_status はハードコードせず store が返す遷移前の実状態を使う（冪等呼び出し
    # — 既に retired だった場合 — でも監査が事実と食い違わない）。
    _audit(AUDIT_ENTITY_LIBRARY_ENTRY, entry_id, previous_status, library_schema.STATUS_RETIRED, uid, {"domain_key": entry["domain_key"]})
    if previous_status != library_schema.STATUS_RETIRED:
        _notify_citers_of_retirement(entry_id, entry["domain_key"], uid)
    return entry


@router.post("/entries/{entry_id}/restore")
def restore_entry(entry_id: str, current_user: dict = Depends(_require_teacher)):
    uid = _uid(current_user)
    try:
        entry, previous_status = library_store.restore_entry(entry_id, updated_by=uid)
    except LibraryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    _audit(AUDIT_ENTITY_LIBRARY_ENTRY, entry_id, previous_status, library_schema.STATUS_ACTIVE, uid, {"domain_key": entry["domain_key"]})
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


# ---------------------------------------------------------------------------
# 概念レジストリ（Phase 3 / migration 082）— 確定・ラベル・関係・地図との対応
#
# 正本: docs/features/concept_registry_design.md §9（不変条項 KR1〜KR10 は §2）。
# すべて TEACHER 以上（KR10）。行削除ルートは無い（KR7）— 見送りは状態遷移で表す。
# 監査は既存 AUDIT_ENTITY_LIBRARY_ENTRY を流用する（新 entity_type を作らない・§11）。
# 数値（cosine / confidence / 候補数）は返さない（KR6）。
# ---------------------------------------------------------------------------


def _registry_audit(**kwargs: Any) -> None:
    """``core/library/registry.py`` が注入で呼ぶ監査記帳（core は services を知らない）。"""
    _audit(
        str(kwargs.get("entity_type") or AUDIT_ENTITY_LIBRARY_ENTRY),
        str(kwargs.get("entity_id") or ""),
        str(kwargs.get("old_status") or ""),
        str(kwargs.get("new_status") or ""),
        kwargs.get("actor_id"),
        {
            **dict(kwargs.get("metadata") or {}),
            "action": kwargs.get("action"),
            "reason": kwargs.get("reason") or "",
        },
    )


@router.post("/entries/{entry_id}/review")
def review_entry(
    entry_id: str,
    payload: EntryReviewRequest,
    current_user: dict = Depends(_require_teacher),
):
    """概念（候補）を確定 / 見送り / 差し戻す（KR2: 確定は人間のみ）。

    ``confirmed`` になって初めて凍結でき、凍結して初めてパイプライン retrieval と
    学習者に届く。``dismissed`` は理由必須（空は 422）。``status='retired'``（公開を
    止める）とは別軸なので、この操作では ``status`` 列は変わらない。
    """
    uid = _uid(current_user)
    _get_entry_or_404(entry_id)
    try:
        entry = library_registry.decide_entry_review(
            entry_id,
            status=payload.status,
            actor_id=str(uid or ""),
            review_note=payload.review_note,
            record_audit=_registry_audit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="library entry not found")
    return {"entry": entry}


@router.get("/entries/{entry_id}/labels")
def list_entry_labels(
    entry_id: str,
    include_dismissed: bool = False,
    current_user: dict = Depends(_require_teacher),
):
    """別名（alternate）・隠しラベル（hidden）の一覧。

    ``preferred`` は ``library_entries.name`` が正本なので行として存在しない（§4.3）。
    """
    _get_entry_or_404(entry_id)
    return {
        "labels": library_registry.list_labels(
            entry_id, include_dismissed=include_dismissed
        )
    }


@router.post("/entries/{entry_id}/labels", status_code=201)
def add_entry_label(
    entry_id: str,
    payload: LabelCreateRequest,
    current_user: dict = Depends(_require_teacher),
):
    """別名・隠しラベルを1件足す。

    ``hidden`` は OCR ノイズ・旧表記を**捨てずに検索から隠す**器（SKOS hiddenLabel・KR7）。
    """
    uid = _uid(current_user)
    _get_entry_or_404(entry_id)
    try:
        label = library_registry.add_label(
            entry_id,
            kind=payload.kind,
            label=payload.label,
            language=payload.language,
            actor_id=str(uid or ""),
            record_audit=_registry_audit,
        )
    except library_registry.RegistryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"label": label}


@router.post("/entries/{entry_id}/labels/{label_id}/dismiss")
def dismiss_entry_label(
    entry_id: str,
    label_id: str,
    payload: LabelDismissRequest = Body(default=LabelDismissRequest()),
    current_user: dict = Depends(_require_teacher),
):
    """ラベルを見送る（理由必須・**行は消さない** — KR7）。"""
    uid = _uid(current_user)
    _get_entry_or_404(entry_id)
    try:
        label = library_registry.dismiss_label(
            label_id,
            actor_id=str(uid or ""),
            review_note=payload.review_note,
            record_audit=_registry_audit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if label is None or label["entry_id"] != entry_id:
        raise HTTPException(status_code=404, detail="label not found")
    return {"label": label}


def _entry_names(entry_ids: list[str]) -> dict[str, str]:
    """関係の端点に表示名を添えるための名前引き（内部 ID を画面に出さないため）。"""
    names: dict[str, str] = {}
    for entry_id in {e for e in entry_ids if e}:
        entry = library_store.get_entry(entry_id)
        if entry:
            names[entry_id] = entry["name"]
    return names


@router.get("/relations")
def list_relations(
    entry_id: str | None = None,
    include_dismissed: bool = False,
    current_user: dict = Depends(_require_teacher),
):
    """関係（broader / related / exact_match / close_match）の一覧。

    **リンクであってマージではない**（KR3）— 2つの概念は並存したままで、
    ``exact_match`` は「同じと言える」という記録にすぎない。
    """
    relations = library_registry.list_relations(
        entry_id=entry_id, include_dismissed=include_dismissed
    )
    names = _entry_names(
        [r["subject_entry_id"] for r in relations] + [r["object_entry_id"] for r in relations]
    )
    for relation in relations:
        relation["subject_name"] = names.get(relation["subject_entry_id"], "")
        relation["object_name"] = names.get(relation["object_entry_id"], "")
    return {"relations": relations}


@router.post("/relations", status_code=201)
def create_relation(
    payload: RelationCreateRequest,
    current_user: dict = Depends(_require_teacher),
):
    """関係を手動で1件作る（``manual_curation`` の候補として立つ）。

    教員が作った候補もいったん ``candidate`` で、確定は ``/relations/{id}/decide``
    での明示操作（KR2: 生成と確定を分ける）。
    """
    uid = _uid(current_user)
    for entry_id in (payload.subject_entry_id, payload.object_entry_id):
        _get_entry_or_404(entry_id)
    try:
        relation = library_registry.create_relation(
            subject_entry_id=payload.subject_entry_id,
            object_entry_id=payload.object_entry_id,
            kind=payload.kind,
            mapping_justification=library_schema.JUSTIFICATION_MANUAL,
            reason=payload.reason,
            actor_id=str(uid or ""),
            record_audit=_registry_audit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"relation": relation}


@router.post("/relations/{relation_id}/decide")
def decide_relation(
    relation_id: str,
    payload: DecideRequest,
    current_user: dict = Depends(_require_teacher),
):
    """関係の候補を確定 / 見送り / 差し戻す（見送りは理由必須）。"""
    uid = _uid(current_user)
    try:
        relation = library_registry.decide_relation(
            relation_id,
            status=payload.status,
            actor_id=str(uid or ""),
            review_note=payload.review_note,
            record_audit=_registry_audit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if relation is None:
        raise HTTPException(status_code=404, detail="relation not found")
    return {"relation": relation}


@router.get("/atlas-links")
def list_atlas_links(
    domain_key: str | None = None,
    entry_id: str | None = None,
    include_dismissed: bool = False,
    current_user: dict = Depends(_require_teacher),
):
    """概念 ↔ 分野の地図（骨格 node）の対応一覧（**版非依存** = KR9）。

    リンクは骨格の版を持たないので、読み時に現行凍結版へ node が実在するかだけを
    ``node_in_current_version`` として添える（版が変わっても行は消さない）。骨格が
    読めないときは ``None``（「分からない」を false と偽らない）。
    """
    links = library_registry.list_node_links(
        domain_key=domain_key, entry_id=entry_id, include_dismissed=include_dismissed
    )
    skeleton = None
    version = ""
    resolve = None
    if domain_key:
        session = get_session()
        try:
            skeleton = atlas_store.load_frozen_skeleton(session, domain_key)
        except Exception:  # noqa: BLE001 — 骨格が読めなくても一覧は返す（fail-soft）
            logger.debug("frozen skeleton lookup failed for %s", domain_key, exc_info=True)
            skeleton = None
        try:
            # 骨格の改版で id が振り直されたリンクも現行版へ読み替える
            # （ノード版間対応 §6・NC8。リンク行は書き換えない）。
            resolve = atlas_correspondence.build_node_resolver(
                atlas_store.load_frozen_history(session, domain_key)
            ).resolve
        except Exception:  # noqa: BLE001
            logger.debug("frozen history lookup failed for %s", domain_key, exc_info=True)
            resolve = None
        finally:
            session.close()
        version = str(getattr(skeleton, "version", "") or "")
    links = library_registry.annotate_node_links(links, skeleton, resolve=resolve)
    names = _entry_names([link["entry_id"] for link in links])
    for link in links:
        link["entry_name"] = names.get(link["entry_id"], "")
    return {"links": links, "skeleton_version": version}


@router.post("/atlas-links/{link_id}/decide")
def decide_atlas_link(
    link_id: str,
    payload: DecideRequest,
    current_user: dict = Depends(_require_teacher),
):
    """地図との対応候補を確定 / 見送り / 差し戻す。

    確定しても骨格（``atlas_skeletons``）は変わらない（KR2 / LS7 / AB4: 対応の記録で
    あって座標系の書き換えではない）。
    """
    uid = _uid(current_user)
    try:
        link = library_registry.decide_node_link(
            link_id,
            status=payload.status,
            actor_id=str(uid or ""),
            review_note=payload.review_note,
            record_audit=_registry_audit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if link is None:
        raise HTTPException(status_code=404, detail="atlas link not found")
    return {"link": link}


class AtlasLinkDeriveRequest(BaseModel):
    """地図との対応候補の導出（§6.1）。分野キーだけを受ける。"""

    domain_key: str


@router.post("/atlas-links/derive")
def derive_atlas_links(
    payload: AtlasLinkDeriveRequest,
    current_user: dict = Depends(_require_teacher),
):
    """概念 ↔ 分野の地図 node の対応候補を**決定論的に**導出する（§6.1）。

    入力は現行凍結骨格 / **保存済み**アンカーベクトル / 確定済みエントリとその凍結版
    embedding / ラベル表 / 教員確定別名だけで、**embedding API も LLM も呼ばない**
    （KR5）。書き込みは候補行（``review_status='candidate'`` のエントリと
    ``status='candidate'`` の node リンク）の upsert のみで、``atlas_skeletons`` には
    一切触れない（KR2 / LS7 / AB4）。

    retired なドメインは 409（読み取り専用 — atlas の既存規約と同型）。現行凍結版が
    無ければ 422（数値なしの事実文）。

    戻り値は §9.1 の ``{candidates, facts}`` + 骨格版。core が返す ``coverage``
    （母集合と処理数の件数報告）は**載せない** — 教員に件数を見せないため（KR6）。
    取りこぼしは ``facts`` の事実文が担う。
    """
    domain_key = (payload.domain_key or "").strip()
    if not domain_key:
        raise HTTPException(status_code=422, detail="分野を指定してください。")

    session = get_session()
    try:
        try:
            lifecycle = atlas_store.domain_lifecycle(session, domain_key)
        except Exception:  # noqa: BLE001 — lifecycle が読めないときは従来どおり続行
            logger.debug("domain lifecycle lookup failed for %s", domain_key, exc_info=True)
            lifecycle = "active"
        if lifecycle == "retired":
            raise HTTPException(
                status_code=409,
                detail="この分野は使用を終了しています。再開してから対応を導出してください。",
            )
        try:
            result = library_atlas_links.derive_node_link_candidates(
                session, domain_key=domain_key
            )
        except library_atlas_links.SkeletonUnavailableError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _audit(
        AUDIT_ENTITY_LIBRARY_ENTRY,
        domain_key,
        "",
        "derived",
        _uid(current_user),
        {
            "action": library_schema.AUDIT_ACTION_NODE_LINK_DERIVE,
            "domain_key": domain_key,
            "skeleton_version": result.get("skeleton_version", ""),
        },
    )
    return {
        "candidates": result.get("candidates", []),
        "skeleton_version": result.get("skeleton_version", ""),
        "facts": result.get("facts", []),
    }


def _document_titles(document_ids: list[str]) -> dict[str, str]:
    """``{document_id: title}``（内部 ID を画面に出さないための名前引き）。"""
    keys = [d for d in {str(d or "") for d in document_ids} if d]
    if not keys:
        return {}
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                "SELECT id::text, COALESCE(title, '') FROM documents "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": keys},
        ).fetchall()
    except Exception:  # noqa: BLE001 — タイトルが引けなくても一覧は返す
        logger.debug("document title lookup failed", exc_info=True)
        return {}
    finally:
        session.close()
    return {str(row[0]): str(row[1] or "") for row in rows}


@router.get("/identity-candidates")
def list_identity_candidates(
    domain_key: str | None = None,
    include_dismissed: bool = False,
    current_user: dict = Depends(_require_teacher),
):
    """同一性候補のレビューキュー（§6.2 / §9.1）。

    candidate なエントリ（``include_dismissed=true`` で見送り済みも）ごとに、そこへ
    張られた同一性リンクを集める。**閲覧不可 document 由来のリンクは除外**し、隠した
    件数を ``hidden_count`` として正直に返す（KR10 / W-β と同型 — 件数は「隠し方の
    事実」であって指標ではない）。

    リンクの確定は既存 ``POST /api/admin/deliberation/identity-links/{id}/confirm|reject``
    を再利用する（新設しない）。エントリを確定してもリンクは自動確定しない
    （1 リンク = 1 判断 = KR2）。
    """
    uid = str(_uid(current_user) or "")
    statuses = [library_schema.REVIEW_STATUS_CANDIDATE]
    if include_dismissed:
        statuses.append(library_schema.REVIEW_STATUS_DISMISSED)

    entries = [
        entry
        for entry in library_store.list_entries(
            domain_key=domain_key, include_retired=True, include_candidates=True
        )
        if entry.get("review_status") in statuses
    ]

    access_cache: dict[str, bool] = {}

    def _can_view(document_id: str) -> bool:
        if not document_id:
            return False
        if document_id not in access_cache:
            try:
                access_cache[document_id] = bool(
                    services.resolve_document_access(uid, document_id).can_view
                )
            except Exception:  # noqa: BLE001 — 判定できないものは見せない（fail-closed）
                logger.debug("document access check failed", exc_info=True)
                access_cache[document_id] = False
        return access_cache[document_id]

    candidates: list[dict] = []
    for entry in entries:
        links = _identity_links.list_for_shared_part(entry["id"])
        visible: list[dict] = []
        hidden = 0
        for link in links:
            document_id = str(link.get("instance_document_id") or "")
            if not _can_view(document_id):
                hidden += 1
                continue
            visible.append(link)
        titles = _document_titles([l.get("instance_document_id") for l in visible])
        supporting_titles: list[str] = []
        items: list[dict] = []
        for link in visible:
            document_id = str(link.get("instance_document_id") or "")
            title = titles.get(document_id, "")
            if title and title not in supporting_titles:
                supporting_titles.append(title)
            items.append(
                {
                    "link_id": link["id"],
                    "instance": {
                        "element_type": link.get("instance_element_type") or "",
                        "element_id": link.get("instance_element_id") or "",
                        "document_id": document_id,
                    },
                    "document_title": title,
                    "local_expression": link.get("local_expression") or {},
                    "status": link.get("status") or "",
                    "mapping_justification": link.get("mapping_justification"),
                }
            )
        if not items and hidden == 0 and not include_dismissed:
            # リンクがまだ 1 本も無い候補（entry だけ先にできた状態）は出さない
            # （教員が判断する材料が無いため）。
            continue
        candidates.append(
            {
                "entry": {
                    "id": entry["id"],
                    "name": entry["name"],
                    "entry_type": entry["entry_type"],
                    "review_status": entry["review_status"],
                    "domain_key": entry["domain_key"],
                    "mapping_justification": entry.get("mapping_justification"),
                },
                "links": items,
                "supporting_titles": supporting_titles,
                "hidden_count": hidden,
            }
        )

    facts: list[str] = []
    if not candidates:
        facts.append("確認をお待ちしている同一性の候補はありません。")
    return {"candidates": candidates, "facts": facts}
