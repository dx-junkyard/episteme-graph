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

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError

import services
from dependencies import _require_teacher

from core.schema import AUDIT_ENTITY_LIBRARY_ENTRY
from core.deliberation import identity_links as _identity_links
from core.deliberation.schema import ELEMENT_THEORY_COMPONENT as _ELEMENT_THEORY_COMPONENT
from core.deliberation.schema import ELEMENT_THEORY_MODULE as _ELEMENT_THEORY_MODULE
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

#: 同一性の候補の空の状態（IK-0864。正本はサーバ — admin.js はこの facts を描くだけ）。
IDENTITY_CANDIDATES_EMPTY = "確認をお待ちしている同一性の候補はありません。"
IDENTITY_CANDIDATES_EMPTY_WITH_DISMISSED = "見送り済みを含めても、同一性の候補はありません。"
IDENTITY_CANDIDATES_ORIGIN_FACT = (
    "候補は、教材の解析の最後の段（共通する概念の候補づくり）で、ほかの論文の部品や登録済みのエントリと"
    "名前・構造が一致したときに作られます。教材を追加したあとは、教材管理の行の"
    "「パイプラインを実行 ▼」から「共通する概念の候補づくり」を実行し直すと、改めて探します。"
)

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


class RelationDecisionReasonRequest(BaseModel):
    """理由の無い確定に理由を書き足す（第 25 周 第 10 波・確定した本人のみ）。"""

    reason: str
    viewer_entry_id: str = ""


class DecideRequest(BaseModel):
    """関係 / node リンクの判断（``confirmed`` / ``dismissed`` / ``candidate``）。"""

    status: str
    review_note: str = ""
    #: IK-1063: 関係の判断の応答で、向きをどの概念から見て言うか（任意。既定は subject）。
    viewer_entry_id: str = ""


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


def _document_access_checker(user_id: str):
    """``document_id -> bool``（閲覧可否）を返すメモ化クロージャ。

    判定できないもの（例外・空 ID）は**見せない**（fail-closed）。core 側は FastAPI も
    ``services`` も import しないので、可視性は必ずこの route 層で掛ける。
    """
    cache: dict[str, bool] = {}

    def _can_view(document_id: Any) -> bool:
        key = str(document_id or "")
        if not key:
            return False
        if key not in cache:
            try:
                cache[key] = bool(services.resolve_document_access(user_id, key).can_view)
            except Exception:  # noqa: BLE001 — 判定できないものは見せない（fail-closed）
                logger.debug("document access check failed", exc_info=True)
                cache[key] = False
        return cache[key]

    return _can_view


def _apply_candidate_visibility(entries: list[dict], user_id: str) -> tuple[list[dict], int]:
    """AI が立てた候補（``review_status='candidate'``）だけに可視性を掛ける（P3-R2）。

    候補エントリは**パイプラインが**論文から起こすので ``source_document_ids`` に
    「その教員が閲覧できない論文」が入り得る。確定済み（``confirmed``）のエントリは
    教員が分野の共同財として確定したもので、従来どおり全教員に見える（後方互換）。

    候補については、閲覧できない出所を落としたうえで、**出所が 1 件も残らない候補は
    一覧から外し**、外した件数を ``hidden_count`` として正直に返す
    （``identity-candidates`` と同じ規律 = KR10）。
    """
    can_view = _document_access_checker(user_id)
    visible: list[dict] = []
    hidden = 0
    for entry in entries:
        if entry.get("review_status") != library_schema.REVIEW_STATUS_CANDIDATE:
            visible.append(entry)
            continue
        sources = [str(d or "") for d in (entry.get("source_document_ids") or []) if d]
        allowed = [d for d in sources if can_view(d)]
        if sources and not allowed:
            hidden += 1
            continue
        visible.append({**entry, "source_document_ids": allowed})
    return visible, hidden


def _get_entry_or_404(entry_id: str) -> dict:
    entry = library_store.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="library entry not found")
    return entry


# ---------------------------------------------------------------------------
# 表示語（IK-0866・2026-10-23）— 一覧・詳細に UUID・取込経路のトークン・分野の英字キー・
# 楽観ロックの revision を見せないため、route 層で表示語を足す（値は残す — 画面は表示語を使う）。
# 帰属の表示名の解決は分野の地図タブ（IK-0850）と同じ ``core.atlas_review_display`` を共有する。
# ---------------------------------------------------------------------------

#: 同梱シード由来の行の帰属の表示語（``created_by='bundled_import'``）。
LIBRARY_ACTOR_BUNDLED = "同梱の初期データ"
#: 「分野を限らない」共通の部品（``domain_key='unassigned'``）の表示語。
LIBRARY_DOMAIN_UNASSIGNED_LABEL = library_schema.DOMAIN_UNASSIGNED_LABEL
#: 名前で探して当たるエントリが無いときの事実文（IK-0868）。
LIBRARY_SEARCH_NOT_FOUND = (
    "「{query}」に当たるエントリはまだありません。右上の「新規エントリ作成」から作れます。"
)
#: 凍結版の有無の事実文。
LIBRARY_VERSION_NONE = "まだ凍結していません"
#: 「いまの扱い」が確定のときの補足（確定と凍結は別の操作）。
# ── 第 5 波（第 25 周 面 A 第 5 周の是正・2026-10-27）の事実文（JS は素通し）─────────
#: IK-0982 / IK-0917: エントリ（概念）の確定の直前に問う 1 文。エントリの確定は「この概念を
#: ライブラリに置くこと」で、部品との結び付き（同一性リンク）の確定ではない。
ENTRY_CONFIRM_QUESTION = (
    "「{name}」を概念としてライブラリに確定しますか。確定するのは概念を置くことだけで、"
    "論文の部品との結び付き（各行）は確定されません。結び付きは行ごとに別に確かめます。"
)
#: IK-0982: エントリを確定したあとの知らせ（未決の結び付きの置き場を言う）。
ENTRY_CONFIRMED_FACT = (
    "概念「{name}」を確定しました。部品との結び付きは別の判断です。候補のまま残っている"
    "結び付きは、同一性の候補の一覧と、このエントリの「結びついた教材の部品」で確認・見送りできます。"
)
ENTRY_DISMISSED_FACT = "概念の候補「{name}」を見送りました（理由と記録は残ります）。"
ENTRY_RESTORED_FACT = "概念「{name}」を候補に戻しました。"
#: IK-0982: 概念は確定済みで、結び付きの判断だけが残っている候補カードの 1 文。
ENTRY_LINKS_PENDING_NOTE = (
    "この概念は確定済みです。部品との結び付きのうち、まだ判断していないものがあります。"
)
#: IK-0980: 同じ論文の別の版どうしを文の近さで結んだ旧候補の 1 文（いまの規則では作らない）。
STALE_SAME_PAPER_PROXY_NOTE = (
    "この候補は、同じ論文の別の版どうしを文の近さで結んだものです。いまの候補づくりでは、"
    "別の版どうしは根拠の式・主張の本文の一致だけで結びます。名前と役割が違えば見送ってください。"
)

LIBRARY_REVIEW_CONFIRMED_NOTE = (
    "概念として確定しています。確定は凍結とは別の操作で、解析が参照するのは凍結した版だけです。"
    "学生の画面（部品の文脈の「共通部品」）に出るのも凍結した版だけで、凍結版が無いうちは学生には出ません。"
)


def _library_version_line(entry: dict) -> str:
    try:
        version_no = int(entry.get("latest_version_no") or 0)
    except (TypeError, ValueError):
        version_no = 0
    return f"凍結版 {version_no}" if version_no > 0 else LIBRARY_VERSION_NONE


def _library_actor_label(actor: Any, labels: dict[str, str]) -> str:
    from core.atlas_review_display import DECISION_ACTOR_UNKNOWN

    text = str(actor or "").strip()
    if not text:
        return ""
    if text == "bundled_import":
        return LIBRARY_ACTOR_BUNDLED
    return labels.get(text) or DECISION_ACTOR_UNKNOWN


def _library_domain_label(session: Any, domain_key: Any, cache: dict[str, str]) -> str:
    key = str(domain_key or "").strip()
    if not key:
        return ""
    if key in cache:
        return cache[key]
    if key == library_schema.DOMAIN_KEY_UNASSIGNED:
        label = LIBRARY_DOMAIN_UNASSIGNED_LABEL
    else:
        from core.atlas_state import domain_display_name

        label = domain_display_name(session, key)
    cache[key] = label
    return label


def _display_entries(entries: list[dict]) -> list[dict]:
    """エントリ（dict）の写しに表示語を足す（入力不変・fail-soft）。"""
    items = [dict(e) for e in entries or () if isinstance(e, dict)]
    if not items:
        return items
    try:
        from core.atlas_review_display import fetch_actor_labels

        session = get_session()
    except Exception:  # noqa: BLE001 — 表示語が引けなくても一覧は返す
        logger.debug("library display labels: session unavailable", exc_info=True)
        return items
    try:
        labels = fetch_actor_labels(
            session,
            [e.get("updated_by") for e in items] + [e.get("created_by") for e in items]
            + [e.get("decided_by") for e in items],
        )
        domain_cache: dict[str, str] = {}
        for item in items:
            item["updated_by_label"] = _library_actor_label(item.get("updated_by"), labels)
            item["created_by_label"] = _library_actor_label(item.get("created_by"), labels)
            item["domain_label"] = _library_domain_label(session, item.get("domain_key"), domain_cache)
            item["version_line"] = _library_version_line(item)
            # IK-1050 の残り: 確定・見送りの判断の記録（判断の印・人・日付）を詳細にも出す。
            # 判断した人の記録が無い確定（同梱・この規則より前）は推測で埋めず「記録なし」と言う（第 9 波）。
            if not item.get("decided_by"):
                unrecorded = library_schema.entry_decision_unrecorded_line(
                    str(item.get("review_status") or "confirmed")
                )
                if unrecorded:
                    item["review_decision_line"] = unrecorded
            if item.get("decided_by"):
                decision = library_schema.relation_decision_line(
                    str(item.get("review_status") or ""),
                    _library_actor_label(item.get("decided_by"), labels),
                    item.get("decided_at"),
                )
                if decision:
                    item["review_decision_line"] = decision
            if str(item.get("review_status") or "confirmed") == library_schema.REVIEW_STATUS_CONFIRMED:
                item["review_status_note"] = LIBRARY_REVIEW_CONFIRMED_NOTE
            elif str(item.get("review_status") or "") == library_schema.REVIEW_STATUS_CANDIDATE:
                # IK-0917 / IK-0982: エントリの確定の直前に問う 1 文。
                item["review_confirm_question"] = ENTRY_CONFIRM_QUESTION.format(
                    name=item.get("name") or ""
                )
    except Exception:  # noqa: BLE001
        logger.warning("library display labels failed (non-fatal)", exc_info=True)
    finally:
        session.close()
    return items


def _display_entry(entry: dict) -> dict:
    out = _display_entries([entry])
    return out[0] if out else entry


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

    P3-R2: 候補行はパイプラインが論文から起こすので、出所に閲覧できない論文が
    混じり得る。候補**だけ**に document 可視性を掛け、出所が 1 件も残らない候補は
    落として ``hidden_count`` で正直に報告する。
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
    entries, hidden_count = _apply_candidate_visibility(entries, str(_uid(current_user) or ""))
    result: dict[str, Any] = {"entries": _display_entries(entries), "hidden_count": hidden_count}
    query = (q or "").strip()
    if query and not entries:
        # IK-0868: 関係の相手を名前で探して見つからないときの事実文（正本はサーバ）。
        result["facts"] = [LIBRARY_SEARCH_NOT_FOUND.format(query=query)]
    return result


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
    return _display_entry(entry)


@router.get("/entries/{entry_id}")
def get_entry(entry_id: str, current_user: dict = Depends(_require_teacher)):
    return _display_entry(_get_entry_or_404(entry_id))


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
    return _display_entry(entry)


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
    domains = [dict(d) for d in library_store.domain_summary()]
    # IK-0866: 分野の英字キーの代わりに分野名を添える（キーは選択の値として残す）。
    try:
        session = get_session()
        try:
            cache: dict[str, str] = {}
            for d in domains:
                d["domain_label"] = _library_domain_label(session, d.get("domain_key"), cache)
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — 名前が引けなくても一覧は返す
        logger.debug("library domain labels unavailable", exc_info=True)
    return {"domains": domains}


@router.post("/entries/similar")
def find_similar_entries(payload: SimilarEntriesRequest, current_user: dict = Depends(_require_teacher)):
    entries = library_search.find_similar_entries(
        domain_key=payload.domain_key,
        text=payload.text,
        entry_type=payload.entry_type,
        top_k=payload.top_k,
        feature=library_search.FEATURE_LIBRARY_SEARCH,
        user_id=current_user.get("id"),
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
    except LibraryRetiredError as exc:
        # P3-R6: retired は読み取り専用（update_entry / freeze_entry と同型）。
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail="library entry not found")
    # IK-0982: 何が済んで何が残ったかを知らせる（文言の正本はここ・JS は素通し）。
    fact_template = {
        library_schema.REVIEW_STATUS_CONFIRMED: ENTRY_CONFIRMED_FACT,
        library_schema.REVIEW_STATUS_DISMISSED: ENTRY_DISMISSED_FACT,
        library_schema.REVIEW_STATUS_CANDIDATE: ENTRY_RESTORED_FACT,
    }.get(str(payload.status or ""), "")
    facts = [fact_template.format(name=entry.get("name") or "")] if fact_template else []
    return {"entry": entry, "facts": facts}


#: IK-0983: 記号の候補づくりの結果（数値なし・JS は素通し）。
SYMBOL_CANDIDATES_CREATED_FACT = (
    "記号の候補を作りました。下の「結びついた記号」で、記号の定義と概念を見比べて確認・見送りできます。"
)
SYMBOL_CANDIDATES_NONE_FACT = (
    "閲覧できる教材の記号の定義の文に、この概念の名前・別名は語として現れませんでした。"
    "別名を足すと、その語でも探します。"
)
#: IK-1153: 名前が語として現れた記号はあるが、どれも既に行がある（新しく作らなかった）とき。
SYMBOL_CANDIDATES_DECIDED_FACT = (
    "判断済みの一致: {items}。定義の文に名前（または別名）が語として現れた記号はありますが、"
    "どれもすでに判断された結び付きなので、新しい候補は作りませんでした。"
    "下の「結びついた記号」の［候補に戻す］から判断し直せます。"
)
SYMBOL_CANDIDATES_EXISTING_FACT = (
    "定義の文に名前（または別名）が語として現れた記号は、すでに下の「結びついた記号」にあります: {items}。"
    "新しい候補は作りませんでした。"
)
SYMBOL_CANDIDATES_NOT_CONFIRMED_DETAIL = "確定した概念にだけ、記号の候補を探します。先に概念を確定してください。"
#: 1 回の候補づくりで照らす閲覧可能な document の上限（重い全件走査を避ける）。
_SYMBOL_DERIVE_DOCUMENT_LIMIT = 200


@router.post("/entries/{entry_id}/symbol-candidates/derive")
def derive_entry_symbol_candidates(
    entry_id: str,
    current_user: dict = Depends(_require_teacher),
):
    """この概念に結びそうな記号の候補を作る（IK-0983・決定論・LLM 0 回・embedding 0 回）。

    閲覧できる教材の ``knowledge_symbols_live`` の定義の文に、このエントリの名前か別名が
    語として現れた記号から candidate リンクを作る。確定は既存のリンクの確定の弁
    （``/api/admin/deliberation/identity-links/{id}/confirm``）だけ（KR2）。
    """
    from core.library.symbol_concepts import derive_symbol_concept_candidates

    uid = str(_uid(current_user) or "")
    entry = _get_entry_or_404(entry_id)
    if entry.get("status") == library_schema.STATUS_RETIRED:
        raise HTTPException(status_code=409, detail="公開を止めたエントリは読み取り専用です。")
    if entry.get("review_status") != library_schema.REVIEW_STATUS_CONFIRMED:
        raise HTTPException(status_code=409, detail=SYMBOL_CANDIDATES_NOT_CONFIRMED_DETAIL)
    try:
        visible = sorted(services.list_visible_document_ids(uid))[:_SYMBOL_DERIVE_DOCUMENT_LIMIT]
    except Exception:  # noqa: BLE001 — 可視性が判定できなければ何も作らない（fail-closed）
        logger.warning("visible documents lookup failed", exc_info=True)
        visible = []
    session = get_session()
    try:
        stats = derive_symbol_concept_candidates(
            session, document_ids=visible, entry_ids=[entry["id"]]
        )
    finally:
        session.close()
    created = int(stats.get("links_created") or 0)
    if created:
        _audit(
            AUDIT_ENTITY_LIBRARY_ENTRY, entry["id"], "", "", uid,
            {"action": "symbol_candidates_derived", "links_created": created},
        )
    return {"facts": [_symbol_derive_fact(created, stats.get("existing_matches") or [])]}


def _symbol_derive_fact(created: int, existing: list) -> str:
    """IK-1153: 新しく作らなかった理由（一致なし／一致は判断済み／一致は既に候補・確定）を言い分ける。"""
    if created:
        return SYMBOL_CANDIDATES_CREATED_FACT
    from core.deliberation import identity_links as _il

    marks = dict(_il.DECISION_MARKS)
    marks.setdefault(_il.IDENTITY_LINK_STATUS_CANDIDATE, "候補")

    def _items(rows: list) -> str:
        seen: list[str] = []
        for row in rows:
            item = f"{row.get('symbol') or '記号'}（{marks.get(str(row.get('status') or ''), '判断済み')}）"
            if item not in seen:
                seen.append(item)
        return "・".join(seen)

    decided = [r for r in existing if str(r.get("status") or "") in ("rejected", "revoked")]
    if decided:
        return SYMBOL_CANDIDATES_DECIDED_FACT.format(items=_items(decided))
    if existing:
        return SYMBOL_CANDIDATES_EXISTING_FACT.format(items=_items(existing))
    return SYMBOL_CANDIDATES_NONE_FACT


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
    labels = library_registry.list_labels(entry_id, include_dismissed=include_dismissed)
    if include_dismissed:
        _attach_label_decision_lines(labels)
    return {"labels": labels}


#: IK-1024: 見送り済みのラベルの行（理由・見送った人・日時）。日時は日付まで。
LABEL_DISMISSED_LINE = "見送り: {reason}（{actor}・{date}）"
LABEL_DISMISSED_FACT = (
    "「{label}」を見送りました。見送り済みの行は、この区画の「見送り済みのラベル」に理由とともに残り、"
    "［見送りを取り消す］で戻せます。"
)
LABEL_RESTORED_FACT = "「{label}」の見送りを取り消しました（理由は記録に残ります）。"


def _attach_label_decision_lines(labels: list[dict]) -> None:
    """見送り済みの行に ``decision_line``（理由・見送った人・日付）を添える（fail-soft・内部 ID を出さない）。"""
    dismissed = [l for l in labels if str(l.get("status") or "") == library_schema.LABEL_STATUS_DISMISSED]
    if not dismissed:
        return
    labels_by_actor: dict[str, str] = {}
    try:
        from core.atlas_review_display import fetch_actor_labels

        session = get_session()
        try:
            labels_by_actor = fetch_actor_labels(session, [l.get("decided_by") for l in dismissed])
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — 名前が引けなくても理由は出す
        logger.debug("label decider labels unavailable", exc_info=True)
    for label in dismissed:
        actor = _library_actor_label(label.get("decided_by"), labels_by_actor) or "記録なし"
        date = str(label.get("updated_at") or "")[:10]
        label["decided_by_label"] = actor
        label["decision_line"] = LABEL_DISMISSED_LINE.format(
            reason=str(label.get("review_note") or "（理由の記録なし）"),
            actor=actor,
            date=date or "日時の記録なし",
        )


#: ラベル追加の結果の事実文（IK-0897。JS にミラーしない）。
LABEL_OUTCOME_FACTS = {
    ("alternate", "created"): "別名を追加しました。",
    ("hidden", "created"): "隠しラベルを追加しました。",
    ("alternate", "existing"): "この別名は既に登録されています。変更はありません。",
    ("hidden", "existing"): "この隠しラベルは既に登録されています。変更はありません。",
    # IK-1024 の残り（第 7 波）: 足し直しでは見送りを取り消さないことに加えて、戻す手を指す。
    ("alternate", "dismissed"): (
        "この別名は見送り済みです。追加し直しても見送りは取り消しません。"
        "戻すときは、この区画の「見送り済みのラベル」を開き、その行の［見送りを取り消す］から理由を書いて戻します。"
    ),
    ("hidden", "dismissed"): (
        "この隠しラベルは見送り済みです。追加し直しても見送りは取り消しません。"
        "戻すときは、この区画の「見送り済みのラベル」を開き、その行の［見送りを取り消す］から理由を書いて戻します。"
    ),
}
#: 関係の記録の結果の事実文（IK-0897）。
RELATION_OUTCOME_FACTS = {
    "created": "関係を候補として記録しました。確定は一覧から行います。",
    "existing": "この関係は既に記録されています。変更はありません。",
    "dismissed": "この関係は見送り済みです。戻すときは見送り済みの一覧から差し戻します。",
}
#: IK-1063 の残り（第 9 波）: 既にある関係を足し直したときに書いた理由の行方（黙って捨てない）。
RELATION_REASON_ADDED_FACT = "この関係は既に記録されていました。書いた理由を、理由の無かったこの関係の記録に足しました。"
RELATION_REASON_KEPT_FACT = (
    "この関係は既に理由付きで記録されています。記録済みの理由は書き換えないため、"
    "書いた理由は保存していません: {reason}"
)
RELATION_REASON_NOT_OWN_FACT = (
    "この関係は既に記録されています。関係を足した人の記録に別の人の理由は足さないため、"
    "書いた理由は保存していません: {reason}"
)


def _relation_readd_reason_fact(relation: dict, reason: str, actor_id: Any) -> tuple[dict, str]:
    """既にある関係を足し直したときの理由の扱い（IK-1063 の残り）。``(relation, fact)``。"""
    text = str(reason or "").strip()
    if not text or text == str(relation.get("reason") or "").strip():
        return relation, ""
    if str(relation.get("reason") or "").strip():
        return relation, RELATION_REASON_KEPT_FACT.format(reason=text)
    filled = library_registry.add_relation_reason(
        str(relation.get("id") or ""),
        reason=text,
        actor_id=str(actor_id or ""),
        record_audit=_registry_audit,
    )
    if filled is None:
        return relation, RELATION_REASON_NOT_OWN_FACT.format(reason=text)
    return filled, RELATION_REASON_ADDED_FACT


def _label_outcome_fact(kind: str, outcome: str) -> str:
    return LABEL_OUTCOME_FACTS.get((str(kind or ""), str(outcome or "")), "")


@router.post("/entries/{entry_id}/labels", status_code=201)
def add_entry_label(
    entry_id: str,
    payload: LabelCreateRequest,
    response: Response,
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
    except LibraryRetiredError as exc:
        # P3-R6: retired は読み取り専用（update_entry / freeze_entry と同型）。
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # IK-0897: 結果（新規 / 既存 / 見送り済み）を事実文で返す。新規だけ 201。
    label = dict(label)
    outcome = str(label.pop("outcome", "") or library_registry.OUTCOME_CREATED)
    if outcome != library_registry.OUTCOME_CREATED:
        response.status_code = 200
    return {"label": label, "outcome": outcome, "facts": [_label_outcome_fact(payload.kind, outcome)]}


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
    return {"label": label, "facts": [LABEL_DISMISSED_FACT.format(label=label.get("label") or "")]}


@router.post("/entries/{entry_id}/labels/{label_id}/restore")
def restore_entry_label(
    entry_id: str,
    label_id: str,
    payload: LabelDismissRequest = Body(default=LabelDismissRequest()),
    current_user: dict = Depends(_require_teacher),
):
    """見送ったラベルを戻す（IK-1024・理由必須・行は 1 本のまま・監査 ``label_restore``）。"""
    uid = _uid(current_user)
    _get_entry_or_404(entry_id)
    try:
        label = library_registry.restore_label(
            label_id,
            actor_id=str(uid or ""),
            review_note=payload.review_note,
            record_audit=_registry_audit,
        )
    except LibraryRetiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if label is None or label["entry_id"] != entry_id:
        raise HTTPException(status_code=404, detail="見送り済みのラベルが見つかりません。")
    return {"label": label, "facts": [LABEL_RESTORED_FACT.format(label=label.get("label") or "")]}


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
    _describe_relations(relations, entry_id, viewer_user_id=_uid(current_user))
    return {"relations": relations}


def _describe_relations(
    relations: list[dict], viewer_entry_id: str | None, *, viewer_user_id: Any = None,
) -> None:
    """関係の行に判断の記録（人・日付・理由）と向きの文を添える（IK-1063・fail-soft）。

    同一性リンクの ``decision_line`` と同じ形。表示名は本人が登録した名前だけ
    （``fetch_actor_labels``）で、UUID は値として残るだけ（画面の文には出さない）。
    """
    if not relations:
        return
    from core.atlas_review_display import fetch_actor_labels

    ids = {
        str(r.get(k) or "").strip()
        for r in relations for k in ("created_by", "decided_by")
    } - {""}
    labels: dict[str, str] = {}
    if ids:
        session = get_session()
        try:
            labels = fetch_actor_labels(session, list(ids))
        except Exception:  # noqa: BLE001 — 表示の補助（fail-soft）
            labels = {}
        finally:
            session.close()
    viewer = str(viewer_entry_id or "").strip()
    # IK-1151: 閲覧者のユーザー id は別の名前で 1 回だけ作る（viewer を上書きしない）。
    viewer_uid = str(viewer_user_id or "").strip()
    for r in relations:
        is_subject = (not viewer) or r.get("subject_entry_id") == viewer
        counterpart = r.get("object_name") if is_subject else r.get("subject_name")
        r["counterpart_name"] = counterpart or ""
        r["kind_label"] = library_schema.relation_kind_label_for_viewer(
            r.get("kind") or "", viewer_is_subject=is_subject
        )
        r["direction_line"] = library_schema.relation_direction_line(
            r.get("kind") or "", viewer_is_subject=is_subject, counterpart_name=counterpart or ""
        )
        decided = str(r.get("decided_by") or "").strip()
        created = str(r.get("created_by") or "").strip()
        decided_label = _library_actor_label(decided, labels)
        created_label = _library_actor_label(created, labels)
        r["decision_line"] = library_schema.relation_decision_line(
            r.get("status") or "", decided_label, r.get("decided_at")
        )
        r["decision_reason_line"] = library_schema.relation_decision_reason_line(
            r.get("status") or "", r.get("review_note")
        )
        r["added_line"] = library_schema.relation_added_line(
            created_label, r.get("created_at"), r.get("reason")
        )
        # 第 25 周 第 10 波: 理由の無い確定に、確定した本人だけが後から理由を書き足せる。
        if (
            viewer_uid
            and str(r.get("status") or "") == "confirmed"
            and not str(r.get("review_note") or "").strip()
            and decided == viewer_uid
        ):
            r["can_add_decision_reason"] = True
            r["decision_reason_add_note"] = library_schema.RELATION_DECISION_REASON_ADD_NOTE


@router.post("/relations", status_code=201)
def create_relation(
    payload: RelationCreateRequest,
    response: Response,
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
    except LibraryRetiredError as exc:
        # P3-R6: retired は読み取り専用（update_entry / freeze_entry と同型）。
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # IK-0897: 結果（新規 / 既存 / 見送り済み）を事実文で返す。新規だけ 201。
    relation = dict(relation)
    outcome = str(relation.pop("outcome", "") or library_registry.OUTCOME_CREATED)
    if outcome != library_registry.OUTCOME_CREATED:
        response.status_code = 200
    facts = [RELATION_OUTCOME_FACTS.get(outcome, "")]
    if outcome == library_registry.OUTCOME_EXISTING:
        # IK-1063 の残り: 足し直しで書いた理由を黙って捨てない（本人の空欄なら足す・それ以外は言う）。
        relation, reason_fact = _relation_readd_reason_fact(relation, payload.reason, uid)
        if reason_fact:
            facts = [reason_fact]
    return {
        "relation": relation,
        "outcome": outcome,
        "facts": facts,
    }


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
    # IK-1063: 判断の直後にも、誰が・いつ・なぜ判断したかを同じ形で返す。
    names = _entry_names([relation["subject_entry_id"], relation["object_entry_id"]])
    relation["subject_name"] = names.get(relation["subject_entry_id"], "")
    relation["object_name"] = names.get(relation["object_entry_id"], "")
    _describe_relations(
        [relation], payload.viewer_entry_id or relation["subject_entry_id"], viewer_user_id=uid,
    )
    facts = [relation["decision_line"]] if relation.get("decision_line") else []
    return {"relation": relation, "facts": facts}


@router.patch("/relations/{relation_id}/decision-reason")
def add_relation_decision_reason(
    relation_id: str,
    payload: RelationDecisionReasonRequest,
    current_user: dict = Depends(_require_teacher),
):
    """理由の書かれていない確定の行に、確定した本人が理由を書き足す（第 25 周 第 10 波）。

    書き換え・他人の判断への追記はしない（409 の事実文）。行は消さず、書き足した事実は監査に残る。
    """
    uid = _uid(current_user)
    text = str(payload.reason or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="書き足す理由を書いてください。")
    relation = library_registry.add_relation_decision_reason(
        relation_id,
        reason=text,
        actor_id=str(uid or ""),
        record_audit=_registry_audit,
    )
    if relation is None:
        raise HTTPException(
            status_code=409, detail=library_schema.RELATION_DECISION_REASON_NOT_ALLOWED,
        )
    relation = dict(relation)
    names = _entry_names([relation["subject_entry_id"], relation["object_entry_id"]])
    relation["subject_name"] = names.get(relation["subject_entry_id"], "")
    relation["object_name"] = names.get(relation["object_entry_id"], "")
    _describe_relations(
        [relation], payload.viewer_entry_id or relation["subject_entry_id"], viewer_user_id=uid,
    )
    return {
        "relation": relation,
        "facts": [library_schema.RELATION_DECISION_REASON_ADDED_FACT],
    }


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


@router.get("/atlas-links/nodes")
def search_atlas_link_nodes(
    domain_key: str,
    q: str = "",
    current_user: dict = Depends(_require_teacher),
):
    """手動の対応付け用: 現行凍結骨格の概念を名前で探す（IK-0927・読み取りのみ）。

    骨格は読むだけで書き換えない（KR2 / VA9）。凍結版が無ければ空の一覧と事実文。
    """
    domain = (domain_key or "").strip()
    if not domain:
        raise HTTPException(status_code=422, detail="分野を指定してください。")
    session = get_session()
    try:
        skeleton = atlas_store.load_frozen_skeleton(session, domain)
    except Exception:  # noqa: BLE001 — 読めなければ空（fail-soft）
        logger.debug("frozen skeleton lookup failed for %s", domain, exc_info=True)
        skeleton = None
    finally:
        session.close()
    if skeleton is None:
        return {
            "nodes": [],
            "skeleton_version": "",
            "facts": ["この分野には、凍結された地図がまだありません。"],
        }
    nodes = library_atlas_links.search_concept_nodes(skeleton, q)
    facts = [] if nodes else ["この名前の概念は、現行の地図にありません。"]
    return {
        "nodes": nodes,
        "skeleton_version": str(getattr(skeleton, "version", "") or ""),
        "facts": facts,
    }


class AtlasLinkManualRequest(BaseModel):
    """教員が手で付ける地図との対応（IK-0927）。"""

    domain_key: str
    node_id: str
    kind: str = library_schema.RELATION_KIND_EXACT_MATCH
    reason: str = ""


@router.post("/entries/{entry_id}/atlas-links", status_code=201)
def create_manual_atlas_link(
    entry_id: str,
    payload: AtlasLinkManualRequest,
    current_user: dict = Depends(_require_teacher),
):
    """エントリと分野の地図の概念の対応を、教員が手で付ける（IK-0927）。

    候補が出ないときの入口。作る行は ``mapping_justification='manual_curation'`` の
    候補で、既存の確定・見送りの操作で確定する（KR2: 確定は人の操作）。node は現行凍結版に
    実在するものだけ受ける（422）。骨格（``atlas_skeletons``）には書かない。
    """
    domain = (payload.domain_key or "").strip()
    node_id = (payload.node_id or "").strip()
    if not domain or not node_id:
        raise HTTPException(status_code=422, detail="分野と地図の概念を選んでください。")
    session = get_session()
    try:
        skeleton = atlas_store.load_frozen_skeleton(session, domain)
    except Exception:  # noqa: BLE001
        skeleton = None
    finally:
        session.close()
    if skeleton is None or node_id not in library_atlas_links.concept_index(skeleton):
        raise HTTPException(status_code=422, detail="選んだ概念は、現行の地図にありません。")
    uid = _uid(current_user)
    try:
        link = library_registry.create_node_link(
            entry_id=entry_id,
            domain_key=domain,
            node_id=node_id,
            kind=payload.kind,
            mapping_justification=library_schema.JUSTIFICATION_MANUAL,
            reason=(payload.reason or "").strip() or "教員が地図の概念を選んで対応を付けました。",
            actor_id=str(uid or ""),
            record_audit=_registry_audit,
        )
    except LibraryRetiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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


#: 空状態の判定で版を照らす document の上限（重い全件走査を避ける。超えた分は判定しない）。
_IDENTITY_RULES_SCAN_DOCUMENT_LIMIT = 300


def _identity_rules_scan_facts(user_id: str) -> list[str]:
    """候補が空のとき、いまの規則で探していない教材を題名で言う（IK-0957・fail-soft）。

    閲覧できる document の採用 run（completed）の ``identity_candidates`` artifact の
    ``rules_version`` を照らす。判定できないときは何も足さない（「探した」と断定しない）。
    """
    from core.document_pipeline.persistence import resolve_artifact_runs
    from core.library import identity_candidates as _ic

    try:
        visible = sorted(services.list_visible_document_ids(user_id) if user_id else [])
    except Exception:  # noqa: BLE001 — 表示の補助
        logger.debug("visible documents lookup failed", exc_info=True)
        return []
    if not visible:
        return []
    truncated_scan = len(visible) > _IDENTITY_RULES_SCAN_DOCUMENT_LIMIT
    visible = visible[:_IDENTITY_RULES_SCAN_DOCUMENT_LIMIT]
    session = get_session()
    try:
        resolved = resolve_artifact_runs(session, visible, policy="adopted")
    except Exception:  # noqa: BLE001 — 判定できなければ黙る
        logger.debug("identity rules scan lookup failed", exc_info=True)
        return []
    finally:
        session.close()
    analyzed = [d for d, e in resolved.items() if str((e or {}).get("status") or "") == "completed"]
    if not analyzed:
        return []
    stale = _ic.documents_not_scanned_by_current_rules(resolved)
    if not stale:
        return [] if truncated_scan else [_ic.IDENTITY_RULES_ALL_SCANNED_FACT]
    titles_by_id = _document_titles(stale)
    titles = [titles_by_id.get(d) or "" for d in stale]
    titles = [t for t in titles if t]
    limit = _ic.IDENTITY_RULES_UNSCANNED_TITLE_LIMIT
    if not titles:
        return []
    facts = [
        _ic.IDENTITY_RULES_UNSCANNED_FACT.format(
            titles=" / ".join("『" + t + "』" for t in titles[:limit])
        )
    ]
    if len(titles) > limit or truncated_scan:
        facts.append(_ic.IDENTITY_RULES_UNSCANNED_MORE_FACT)
    return facts


def _live_component_ids(links_by_entry: dict[str, list[dict]]) -> set[str]:
    """候補リンクが指す component のうち、いま ``theory_components_live`` にある id（P3-R9）。

    解析のやり直し（supersede）で live から外れた component へのリンクは、教員が
    「どちらの記述か」を確かめられない。判定できない（DB 不達）ときは**全部あるもの
    として扱う**（候補一覧が丸ごと空になるより、従来どおり出すほうが害が小さい）。
    """
    ids = {
        str(link.get("instance_element_id") or "")
        for links in links_by_entry.values()
        for link in links
        if str(link.get("instance_element_type") or "") == _ELEMENT_THEORY_COMPONENT
        and str(link.get("instance_element_id") or "")
    }
    if not ids:
        return set()
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                "SELECT id::text FROM theory_components_live "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": sorted(ids)},
        ).fetchall()
    except Exception:  # noqa: BLE001 — 判定できないときは落とさない（fail-open）
        logger.debug("live component lookup failed", exc_info=True)
        return ids
    finally:
        session.close()
    return {str(row[0]) for row in rows}


def _live_module_ids(links_by_entry: dict[str, list[dict]]) -> set[str]:
    """候補リンクが指す理論モジュール行のうち、いま ``knowledge_theory_modules_live`` にある id。

    理論モジュール層 Phase 1（theory_module_layer_design.md §13.8）。再解析・規則の版の
    引き上げで superseded になった行へのリンクは、component と同じく教員が判断できないので
    落として事実文で報告する（P3-R9 と同じ規律）。判定できない（DB 不達）ときは
    :func:`_live_component_ids` と同じく**全部あるものとして扱う**（fail-open）。
    読むのは live ビューだけ（基表 ``knowledge_theory_modules`` は読まない）。
    """
    ids = {
        str(link.get("instance_element_id") or "")
        for links in links_by_entry.values()
        for link in links
        if str(link.get("instance_element_type") or "") == _ELEMENT_THEORY_MODULE
        and str(link.get("instance_element_id") or "")
    }
    if not ids:
        return set()
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                "SELECT id::text FROM knowledge_theory_modules_live "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": sorted(ids)},
        ).fetchall()
    except Exception:  # noqa: BLE001 — 判定できないときは落とさない（fail-open）
        logger.debug("live theory module lookup failed", exc_info=True)
        return ids
    finally:
        session.close()
    return {str(row[0]) for row in rows}


def _link_resolves_to_live_component(
    link: dict, live_component_ids: set[str], live_module_ids: set[str] | None = None
) -> bool:
    """component / 理論モジュールへのリンクを live 実在で絞る（他の要素型はそのまま通す）。

    IK-1076: 判定の正本は ``identity_links.instance_is_live``（詳細の判断可否と同じ述語）。
    """
    return _identity_links.instance_is_live(
        link.get("instance_element_type"),
        link.get("instance_element_id"),
        live_component_ids,
        live_module_ids,
    )


def _document_display(document_ids: list[Any]) -> dict[str, dict]:
    """``{document_id: {title, version_label, identity}}``（fail-soft）。

    題名は :func:`_document_titles`（正本の名前引き）。``version_label`` は同じ題名の論文が
    2 本以上のときだけ（IK-0956 の一覧側）。``identity`` は「同じ論文の別の版」の判定材料
    （画面には出さない）。版の手がかりが引けなくても題名は返す。
    """
    keys = sorted({str(d or "") for d in document_ids if str(d or "")})
    if not keys:
        return {}
    titles = _document_titles(keys)
    out: dict[str, dict] = {
        key: {"title": titles.get(key, ""), "version_label": "", "identity": {}} for key in keys
    }
    try:
        session = get_session()
    except Exception:  # noqa: BLE001
        return out
    try:
        rows = session.execute(
            sa_text(
                "SELECT d.id::text, COALESCE(d.title, ''), d.filename, d.source_url, d.source_path, "
                "(SELECT count(*) FROM documents o WHERE o.title = d.title) "
                "FROM documents d WHERE d.id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": keys},
        ).fetchall()
    except Exception:  # noqa: BLE001 — 版の手がかりが引けなくても一覧は返す
        logger.debug("document version lookup failed", exc_info=True)
        return out
    finally:
        session.close()
    for row in rows:
        key = str(row[0])
        if key not in out:
            continue
        if len(row) > 5 and int(row[5] or 0) > 1:
            out[key]["version_label"] = _identity_links.document_version_hint(row[2], row[3])
        out[key]["identity"] = {
            "title": str(row[1] or ""), "filename": row[2], "source_url": row[3], "source_path": row[4],
        }
        # IK-1026: 対の確認の問いに版（TeX / PDF）を言う（ファイル名は出さない）。
        out[key]["format_label"] = _identity_links.document_format_label(row[2], row[3])
    return out


def _component_display(links_by_entry: dict[str, list[dict]]) -> dict[str, dict]:
    """リンクが指す部品の要約・種別（``{component_id: {summary, component_type}}``・1 クエリ）。"""
    ids = sorted({
        str(link.get("instance_element_id") or "")
        for links in links_by_entry.values()
        for link in links
        if str(link.get("instance_element_type") or "") == _ELEMENT_THEORY_COMPONENT
        and str(link.get("instance_element_id") or "")
    })
    if not ids:
        return {}
    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                "SELECT id::text, COALESCE(summary, ''), COALESCE(component_type, '') "
                "FROM theory_components_live WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": ids},
        ).fetchall()
    except Exception:  # noqa: BLE001 — 表示の補助（fail-soft）
        logger.debug("component display lookup failed", exc_info=True)
        return {}
    finally:
        session.close()
    return {
        str(row[0]): {"summary": str(row[1] or "")[:160], "component_type": str(row[2] or "")}
        for row in rows
    }


def _link_basis(link: dict) -> str:
    for item in link.get("evidence") or ():
        if isinstance(item, dict) and str(item.get("basis") or ""):
            return str(item.get("basis"))
    return ""


def _identity_candidate_item(
    link: dict, *, entry: dict, document_id: str, display: dict, component: dict | None
) -> dict:
    """候補一覧の 1 リンク行（既存キーは不変・第 5 波で表示の材料を追加）。"""
    from core.library.identity_candidates import SAME_PAPER_BASIS, SAME_PAPER_BASIS_LABEL

    element_type = str(link.get("instance_element_type") or "")
    local = link.get("local_expression") or {}
    instance_label = str(local.get("name") or "") if isinstance(local, dict) else str(local or "")
    item: dict[str, Any] = {
        "link_id": link["id"],
        "instance": {
            "element_type": element_type,
            "element_id": link.get("instance_element_id") or "",
            "document_id": document_id,
        },
        "document_title": display.get("title", ""),
        "local_expression": local,
        "status": link.get("status") or "",
        "mapping_justification": link.get("mapping_justification"),
        # IK-0981: 何を根拠に立ったかの事実文（相手の名前を含めない = KR10）。
        "reason": str(link.get("reason") or ""),
        # 内部の材料（_attach_identity_pair_facts が使って落とす。応答に残さない）。
        "_instance_label": instance_label,
        "_format_label": display.get("format_label", ""),
        "_decided_by": link.get("decided_by") or "",
        "_decided_at": link.get("decided_at"),
        "_revoked_by": link.get("revoked_by") or "",
        "_revoked_at": link.get("revoked_at"),
        # IK-1062: 候補に戻した記録（_attach_identity_pair_facts が restore_line にして落とす）。
        "_restored_by": link.get("restored_by") or "",
        "_restored_at": link.get("restored_at"),
        "_restored_from": link.get("restored_from") or "",
        "_restore_reason": link.get("restore_reason") or "",
        # IK-1062 の残り（第 10 波）: 戻す前の判断の理由（prior_decision_line にして落とす）。
        "_revoke_reason": link.get("revoke_reason") or "",
        # IK-1050: 札の訳語はサーバの表（rejected / revoked まで覆う）。
        "status_label": _identity_links.IDENTITY_LINK_STATUS_LABELS.get(str(link.get("status") or ""), ""),
    }
    # IK-1050: 見送った行の理由（判断の印の後ろ）。
    reason_line = _identity_links.decision_reason_line(link.get("status"), link.get("revoke_reason"))
    if reason_line:
        item["decision_reason_line"] = reason_line
    if str(link.get("status") or "") in _identity_links.RESTORE_VOCABULARIES:
        # IK-1062: 否定の判断の行には戻す手の事実文（［候補に戻す］の前に添える）。
        item["restore_note"] = _identity_links.RESTORE_NOTE
    if _link_basis(link) == SAME_PAPER_BASIS:
        # IK-0981: 版の規則の対は「表記の一致」と呼ばない。
        item["basis_label"] = SAME_PAPER_BASIS_LABEL
    if display.get("version_label"):
        # IK-0956（一覧側）: 同じ題名の論文が並ぶときだけ版の手がかり。
        item["document_version_label"] = display["version_label"]
    if component and component.get("summary"):
        # IK-0980 ③: 候補カードに部品の要約（教員の判断材料）。
        item["instance_summary"] = component["summary"]
    if str(link.get("status") or "") == "candidate":
        # IK-0917: 一覧の［確認］でも、両側を名指しして同じものかを問う。
        item["confirm_question"] = _identity_links.confirm_question(
            _identity_links.INSTANCE_TYPE_DISPLAY.get(element_type, "要素"),
            instance_label,
            str(entry.get("name") or ""),
        )
        note = _identity_links.granularity_note(
            (component or {}).get("component_type"), entry.get("entry_type")
        )
        if note:
            item["granularity_note"] = note
    return item


def _attach_identity_pair_facts(items: list[dict], *, entry: dict, unresolved: bool) -> None:
    """候補カードの行に、対の確認の問い（IK-1026）・相手の置き換わり（IK-1020）・判断の印（IK-0953）。

    表示の補助だけ（判断は既存のリンクの確定・見送りの弁のまま）。入力の ``items`` を書き換える。
    """
    labels: dict[str, str] = {}
    actor_ids = [str(it.get("_decided_by") or "") for it in items if it.get("_decided_by")]
    actor_ids += [str(it.get("_revoked_by") or "") for it in items if it.get("_revoked_by")]
    actor_ids += [str(it.get("_restored_by") or "") for it in items if it.get("_restored_by")]
    if actor_ids:
        try:
            from core.atlas_review_display import fetch_actor_labels

            session = get_session()
            try:
                labels = fetch_actor_labels(session, actor_ids)
            finally:
                session.close()
        except Exception:  # noqa: BLE001 — 名前が引けなくても印と日付は出す
            logger.debug("identity decision actor labels unavailable", exc_info=True)
    for it in items:
        decided_by = str(it.pop("_decided_by", "") or "")
        decided_at = it.pop("_decided_at", None)
        revoked_by = str(it.pop("_revoked_by", "") or "")
        revoked_at = it.pop("_revoked_at", None)
        restored_by = str(it.pop("_restored_by", "") or "")
        restored_at = it.pop("_restored_at", None)
        restored_from = it.pop("_restored_from", "")
        restore_reason = str(it.pop("_restore_reason", "") or "").strip()
        revoke_reason = str(it.pop("_revoke_reason", "") or "").strip()
        if str(it.get("status") or "") == "candidate" and restored_at:
            # IK-1062: 否定の判断から候補に戻した行（戻した人・日付・前の判断・理由）。
            it["restore_line"] = _identity_links.restore_line(
                _library_actor_label(restored_by, labels) if restored_by else "", restored_at, restored_from,
            )
            if restore_reason:
                it["restore_reason_line"] = _identity_links.restore_reason_line(restore_reason)
            # IK-1062 の残り（第 10 波）: 戻す前の判断の誰・いつ・なぜ（記録の列は上書きしていない）。
            prior = _identity_links.prior_decision_line(
                restored_from,
                decided_by_label=_library_actor_label(decided_by, labels) if decided_by else "",
                decided_at=decided_at,
                revoked_by_label=_library_actor_label(revoked_by, labels) if revoked_by else "",
                revoked_at=revoked_at,
                reason=revoke_reason,
            )
            if prior:
                it["prior_decision_line"] = prior
        decided_label = _library_actor_label(decided_by, labels) if decided_by else ""
        line = _identity_links.decision_line(it.get("status"), decided_label, decided_at)
        if line:
            it["decision_line"] = line
        elif str(it.get("status") or "") == "revoked":
            # IK-0953 の残り: 取り消しの行は取り消した人・日付と元の確定を 1 文で。
            it["decision_line"] = _identity_links.revoke_line(
                _library_actor_label(revoked_by, labels) if revoked_by else "",
                revoked_at,
                decided_label,
                decided_at,
            )
    for it in items:
        own_type = str((it.get("instance") or {}).get("element_type") or "")
        partners = [
            o for o in items
            if o is not it
            and str(o.get("status") or "") in ("candidate", "confirmed")
            and str((o.get("instance") or {}).get("element_type") or "") == own_type
        ]
        if str(it.get("status") or "") != "candidate":
            # IK-1050: 判断した行でも、組の相手が置き換わった事実を保存値（「別の論文の…」）より先に言う。
            if unresolved and not partners:
                it["recorded_reason"] = str(it.get("reason") or "")
                it["reason"] = _identity_links.PARTNER_REPLACED_REASON
            continue
        type_label = _identity_links.INSTANCE_TYPE_DISPLAY.get(own_type, "要素")
        side = _identity_links.instance_side_label(it.get("_format_label"), it.get("document_title"))
        if partners:
            it["confirm_question"] = _identity_links.pair_confirm_question(
                instance_type_label=type_label,
                instance_label=str(it.get("_instance_label") or ""),
                instance_side=side,
                partners=[
                    (
                        _identity_links.INSTANCE_TYPE_DISPLAY.get(
                            str((o.get("instance") or {}).get("element_type") or ""), "要素"
                        ),
                        str(o.get("_instance_label") or ""),
                        _identity_links.instance_side_label(o.get("_format_label"), o.get("document_title")),
                    )
                    for o in partners
                ],
                shared_part_name=str(entry.get("name") or ""),
            )
        elif unresolved:
            # 組の相手は再解析で置き換わった — 「別の論文の記述と…」の保存値を言わない。
            it["recorded_reason"] = str(it.get("reason") or "")
            it["reason"] = _identity_links.PARTNER_REPLACED_REASON
            it["confirm_question"] = _identity_links.partner_replaced_question(
                type_label, str(it.get("_instance_label") or ""), side, str(entry.get("name") or "")
            )
    for it in items:
        for key in ("_instance_label", "_format_label"):
            it.pop(key, None)


#: IK-1050: 見送った概念のカードの判断の行（判断の印・人・日付）と、戻す手の案内。
ENTRY_DISMISSED_DECISION_LINE = "〔見送り〕 見送り: {actor}・{date}"
ENTRY_DISMISSED_REASON_LINE = "見送りの理由: {reason}"
ENTRY_DISMISSED_RESTORE_NOTE = (
    "この概念は見送り済みです（この一覧では判断のボタンを出しません）。"
    "候補に戻すときは、エントリの詳細の［候補に戻す］から理由を書いて戻します。"
)


def _dismissed_entry_decision(entry: dict, actor_labels: dict[str, str]) -> dict:
    """見送った概念の判断の記録（理由・誰・いつ）。内部 ID は出さない。"""
    actor = _library_actor_label(entry.get("decided_by"), actor_labels) or "記録なし"
    date = str(entry.get("decided_at") or "")[:10] or "日付の記録なし"
    reason = str(entry.get("review_note") or "").strip() or "記録なし"
    return {
        "decision_line": ENTRY_DISMISSED_DECISION_LINE.format(actor=actor, date=date),
        "decision_reason_line": ENTRY_DISMISSED_REASON_LINE.format(reason=reason),
        "restore_note": ENTRY_DISMISSED_RESTORE_NOTE,
    }


def _is_stale_same_paper_proxy(entry: dict, links: list[dict], doc_display: dict) -> bool:
    """文の近さで立った候補エントリのリンクが同じ論文の別の版どうしか（IK-0980）。"""
    if str(entry.get("mapping_justification") or "") != library_schema.JUSTIFICATION_VECTOR:
        return False
    doc_ids = sorted({
        str(l.get("instance_document_id") or "")
        for l in links
        if str(l.get("mapping_justification") or "") == library_schema.JUSTIFICATION_VECTOR
    })
    if len(doc_ids) < 2:
        return False
    try:
        from core.library.identity_candidates import same_paper

        first = (doc_display.get(doc_ids[0]) or {}).get("identity")
        return bool(first) and all(
            same_paper(first, (doc_display.get(d) or {}).get("identity") or {})
            for d in doc_ids[1:]
            if (doc_display.get(d) or {}).get("identity")
        )
    except Exception:  # noqa: BLE001 — 判定できなければ何も言わない
        return False


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

    # IK-0982: 済み = エントリとリンクの両方が決まったこと。確定済みエントリでも未決のリンクを
    # 持つものはキューに残す（エントリの確定でリンクの判断の置き場を閉じない）。
    all_entries = [
        entry
        for entry in library_store.list_entries(
            domain_key=domain_key, include_retired=True, include_candidates=True
        )
        if entry.get("review_status") in statuses
        or entry.get("review_status") == library_schema.REVIEW_STATUS_CONFIRMED
    ]
    candidate_links_by_entry = _identity_links.list_for_shared_parts([e["id"] for e in all_entries])
    entries = _display_entries([
        entry
        for entry in all_entries
        if entry.get("review_status") in statuses
        or any(
            str(link.get("status") or "") == "candidate"
            for link in candidate_links_by_entry.get(entry["id"], [])
        )
    ])

    _can_view = _document_access_checker(uid)

    # P3-R11: エントリ 1 件ごとに 1 クエリ（N+1）を投げず、まとめて 1 回で引く。
    links_by_entry = {e["id"]: candidate_links_by_entry.get(e["id"], []) for e in entries}
    live_components = _live_component_ids(links_by_entry)
    live_modules = _live_module_ids(links_by_entry)

    candidates: list[dict] = []
    hidden_unresolved_any = False
    doc_display = _document_display(
        [link.get("instance_document_id") for links in links_by_entry.values() for link in links]
    )
    component_facts = _component_display(links_by_entry)
    # IK-1050: 見送った概念のカードに、見送った人・日付・理由を出す（名前はまとめて 1 回で引く）。
    dismissed_actor_labels: dict[str, str] = {}
    dismissed_actors = [
        str(e.get("decided_by") or "")
        for e in entries
        if e.get("review_status") in (
            library_schema.REVIEW_STATUS_DISMISSED, library_schema.REVIEW_STATUS_CONFIRMED,
        ) and e.get("decided_by")
    ]
    if dismissed_actors:
        try:
            from core.atlas_review_display import fetch_actor_labels

            _session = get_session()
            try:
                dismissed_actor_labels = fetch_actor_labels(_session, dismissed_actors)
            finally:
                _session.close()
        except Exception:  # noqa: BLE001 — 名前が引けなくても理由と日付は出す
            logger.debug("dismissed entry actor labels unavailable", exc_info=True)
    for entry in entries:
        entry_confirmed = entry.get("review_status") == library_schema.REVIEW_STATUS_CONFIRMED
        links = links_by_entry.get(entry["id"], [])
        if entry_confirmed:
            # 確定済みのエントリは未決のリンクだけを並べる（残っている判断だけを見せる）。
            links = [l for l in links if str(l.get("status") or "") == "candidate"]
        visible: list[dict] = []
        unresolved: list[dict] = []
        hidden = 0
        hidden_unresolved = False
        for link in links:
            document_id = str(link.get("instance_document_id") or "")
            if not _can_view(document_id):
                hidden += 1
                continue
            if not _link_resolves_to_live_component(link, live_components, live_modules):
                # P3-R9: live に解決できない component へのリンクは教員が判断できない
                # （消えた要素の名前だけが残る）。落として事実文で報告する。
                hidden_unresolved = True
                unresolved.append(link)
                continue
            visible.append(link)
        if hidden_unresolved:
            hidden_unresolved_any = True
        supporting_titles: list[str] = []
        items: list[dict] = []
        for link in visible:
            document_id = str(link.get("instance_document_id") or "")
            display = doc_display.get(document_id) or {}
            title = display.get("title", "")
            if title and title not in supporting_titles:
                supporting_titles.append(title)
            items.append(
                _identity_candidate_item(
                    link,
                    entry=entry,
                    document_id=document_id,
                    display=display,
                    component=component_facts.get(str(link.get("instance_element_id") or "")),
                )
            )
        if not items and hidden == 0 and not hidden_unresolved and not include_dismissed:
            # リンクがまだ 1 本も無い候補（entry だけ先にできた状態）は出さない
            # （教員が判断する材料が無いため）。
            continue
        if entry_confirmed and not items:
            # 確定済みエントリの未決リンクが全部見えない（閲覧不可・解決不能）なら出さない。
            continue
        entry_out = {
            "id": entry["id"],
            "name": entry["name"],
            "entry_type": entry["entry_type"],
            "review_status": entry["review_status"],
            "domain_key": entry["domain_key"],
            "domain_label": entry.get("domain_label") or "",
            "mapping_justification": entry.get("mapping_justification"),
        }
        if entry_confirmed:
            entry_out["links_pending_note"] = ENTRY_LINKS_PENDING_NOTE
            # IK-1050 の残り: 確定済みの概念にも、確定した人・日付（記録があるときだけ）。
            if entry.get("decided_by"):
                confirmed_line = library_schema.relation_decision_line(
                    library_schema.REVIEW_STATUS_CONFIRMED,
                    _library_actor_label(entry.get("decided_by"), dismissed_actor_labels),
                    entry.get("decided_at"),
                )
                if confirmed_line:
                    entry_out["decision_line"] = confirmed_line
            else:
                # IK-1050 の残り（第 9 波）: 記録の無い旧い確定も黙らない（詳細と同じ文）。
                entry_out["decision_line"] = library_schema.ENTRY_CONFIRMED_UNRECORDED_LINE
        elif entry.get("review_status") == library_schema.REVIEW_STATUS_CANDIDATE:
            entry_out["confirm_question"] = ENTRY_CONFIRM_QUESTION.format(name=entry["name"])
        elif entry.get("review_status") == library_schema.REVIEW_STATUS_DISMISSED:
            entry_out.update(_dismissed_entry_decision(entry, dismissed_actor_labels))
        # IK-1076: 判断の対象にできるか（詳細と同じ述語 — 見送った概念の行は判断しない。
        # 置き換わった部品の行は上で落としてある）。
        for it in items:
            note = _identity_links.link_undecidable_note({}, entry_review_status=entry.get("review_status"))
            if note:  # 判断できる行にはキーを足さない（既存の DTO の形を保つ）
                it["decidable"] = False
                it["undecidable_note"] = note
        # IK-1020: 旧規則の判定はリンク全体（再解析で置き換わって隠した行も含む）で行う。
        if _is_stale_same_paper_proxy(entry, visible + unresolved, doc_display):
            entry_out["stale_rule_note"] = STALE_SAME_PAPER_PROXY_NOTE
            # IK-0980: 行の根拠の文も「別の論文の…」と言わない（保存値は recorded_reason）。
            for it in items:
                reworded = _identity_links.reworded_same_paper_reason(
                    it.get("reason"), partner_is_same_paper=True
                )
                if reworded:
                    it["recorded_reason"] = str(it.get("reason") or "")
                    it["reason"] = reworded
        # IK-1026 / IK-1020 / IK-0953: 対の相手を名指しした問い・片側が置き換わった事実・判断の印。
        _attach_identity_pair_facts(items, entry=entry, unresolved=bool(unresolved))
        if unresolved and items:
            entry_out["partner_replaced_note"] = _identity_links.PARTNER_REPLACED_NOTE
        candidates.append(
            {
                "entry": entry_out,
                "links": items,
                "supporting_titles": supporting_titles,
                "hidden_count": hidden,
                "hidden_unresolved": hidden_unresolved,
            }
        )

    facts: list[str] = []
    if not candidates:
        # IK-0864: 見送り済みを含めたかどうかで文を分け、候補の出所と作り直す道を添える
        # （文言の正本はこの facts 1 つ — 画面は自前の固定文を並べない）。
        facts.append(
            IDENTITY_CANDIDATES_EMPTY_WITH_DISMISSED
            if include_dismissed
            else IDENTITY_CANDIDATES_EMPTY
        )
        facts.append(IDENTITY_CANDIDATES_ORIGIN_FACT)
        # IK-0919: 名前の違う同じ論文の別の版（TeX / PDF）も候補になる経路があることを言う
        # （文言の正本は core/library/identity_candidates.py）。
        from core.library.identity_candidates import SAME_PAPER_VERSIONS_NOTE

        facts.append(SAME_PAPER_VERSIONS_NOTE)
        # IK-0957: 「探して無かった」と「まだ探していない」を言い分ける。
        facts.extend(_identity_rules_scan_facts(uid))
    if hidden_unresolved_any:
        # P3-R9: 件数は出さない（KR6）。「何が起きたか」だけを事実文で書く。
        facts.append(
            "解析がやり直されたなどの理由で、いま参照できない要素への候補は表示していません。"
        )
    return {"candidates": candidates, "facts": facts}
