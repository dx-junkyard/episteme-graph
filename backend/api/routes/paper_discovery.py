"""論文ディスカバリー層 — 管理 API（実パス ``/api/admin/discovery/...``）。

正本: ``docs/features/paper_discovery_design.md`` §4.3（API 契約）/ §4.5（監査）。
不変条項 PD1〜PD8 のうち、本ルータが構造として守るもの:

- **PD1 発見は自動、取り込みは教員の明示承認のみ**: 取り込みは ``POST /ingest``
  （教員のリクエスト）だけが入口で、worker・スケジューラ・検索の副作用から
  取り込みを起動する経路を作らない。1リクエストの件数上限
  :data:`MAX_INGEST_PER_REQUEST` を超えたら 422（Phase 2 のバッチを待つ）。
- **PD2 取得・解析は既存経路へ完全合流**: 論文の取得は
  ``core.url_fetch.fetch_source_from_url``（許可リスト照合・SSRF ガード・形式判定）に
  一任し、受理後は ``routes.admin._accept_material_source`` へ合流する。
  **このモジュールは HTTP クライアント（requests / httpx 等）を import しない**。
- **PD3 購読は教員の意思の正本**: サーバが購読条件を書き換えるのは
  ``PUT /subscriptions/{domain_key}``（教員の明示保存）だけ。
  ``/keyphrase-candidates`` は候補を返すだけで購読行に書かない。
- **PD4 数値スコアを見せない**: core の DTO をそのまま返し、類似度・一致度の
  生数値を足さない（``core.paper_discovery.search`` が構造として持たない）。
- **PD5 候補は保存せず読み時導出**: 保存するのは購読条件・見送り記録・取り込み出所
  （``documents.source_url``）だけ。候補一覧のスナップショットを作らない。
- **PD6 閉世界の正直さ**: 検索は core の DTO（``query`` / ``closed_world_note``
  同梱）を素通しし、arXiv API 失敗を空一覧に化けさせない（502 + 事実文）。

エラーは日本語の事実文で、内部情報（解決 IP・スタックトレース）を ``detail`` に
載せない（UF6 継承）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import _require_teacher
from services import record_review_event

from core import url_fetch
from core.paper_discovery import arxiv_client
from core.paper_discovery import schema as pd_schema
from core.paper_discovery import search as pd_search
from core.paper_discovery import store as pd_store
from core.paper_discovery import vocab as pd_vocab
from core.postgres import get_session as _pg_session
from core.schema import AUDIT_ENTITY_PAPER_DISCOVERY

# PD2: 取得と受理は既存経路をそのまま呼ぶ（ディスカバリー専用の取得経路・教材種別を
# 作らない）。``_URL_FETCH_ERROR_STATUS`` も再利用し、写像を二重管理しない。
from routes.admin import (
    _URL_FETCH_ERROR_STATUS,
    _accept_material_source,
    _validate_models_option,
)

logger = logging.getLogger(__name__)

# main.py がそのまま登録する（admin 系の子ルーター群には include しない —
# CLAUDE.md / Tier 3-17c。landscape.py と同じ「直接登録」の扱い）。
router = APIRouter(prefix="/api/admin/discovery", tags=["Paper Discovery"])


# ---------------------------------------------------------------------------
# 定数・事実文
# ---------------------------------------------------------------------------

#: 1リクエストで取り込める論文の上限（PD1 — v1 は同期・少数件）。
MAX_INGEST_PER_REQUEST = 5

#: ``POST /search`` の 1 リクエスト取得件数の上限（arXiv への行儀 — PD7）。
MAX_SEARCH_RESULTS = 100

#: 検索結果の既定件数。
DEFAULT_SEARCH_RESULTS = 50

_DETAIL_INGEST_EMPTY = "取り込む論文が選択されていません。"
_DETAIL_INGEST_TOO_MANY = (
    f"一度に取り込めるのは{MAX_INGEST_PER_REQUEST}件までです。"
    "件数を減らして実行してください。"
)
_DETAIL_INVALID_ARXIV_ID = "arXiv ID として解釈できませんでした。"
_DETAIL_ARXIV_UNAVAILABLE = (
    "arXiv に接続できませんでした。時間をおいて再度お試しください。"
)
_DETAIL_DISMISSAL_NOT_FOUND = "この見送り記録は見つかりません。"

# 監査の status 語彙（``metadata.action`` で操作を区別する — 既存層と同じ流儀）。
_STATUS_NONE = ""
_STATUS_SUBSCRIBED = "subscribed"
_STATUS_CANDIDATE = "candidate"
_STATUS_DISMISSED = "dismissed"
_STATUS_INGEST_REQUESTED = "ingest_requested"

#: 分野が特定できない操作（取り込みは複数分野にまたがり得る）の監査 entity_id。
_ENTITY_ID_FALLBACK = "arxiv"


# ---------------------------------------------------------------------------
# リクエストモデル
# ---------------------------------------------------------------------------


class SubscriptionUpdateRequest(BaseModel):
    """購読条件の作成・更新。

    ``keyphrases`` は文字列配列でも ``{"text", "source", "enabled"}`` の配列でも
    受ける（正規化の正本は ``core.paper_discovery.schema.normalize_keyphrases``）。
    ``enabled=False`` のフレーズも保存する（外した状態を保持する — P4）。
    """

    arxiv_categories: Optional[list] = None
    keyphrases: Optional[list] = None
    followed_authors: Optional[list] = None


class SearchRequest(BaseModel):
    """候補検索。条件を渡すと保存済み購読より優先する（保存せず試せる — PD3）。"""

    domain_key: str = ""
    categories: Optional[list] = None
    keyphrases: Optional[list] = None
    followed_authors: Optional[list] = None
    start: int = 0
    max_results: int = DEFAULT_SEARCH_RESULTS


class IngestItem(BaseModel):
    """取り込み対象1件（arXiv ID。URL 表記・version 付きも正規化して受ける）。"""

    arxiv_id: str


class IngestRequest(BaseModel):
    """取り込み実行（PD1 — 教員の明示承認）。

    ``analyze_images`` / ``models`` の意味は ``POST /api/admin/materials/upload`` と
    同一（画像パイプライン §3 / M層設計書 §7）。
    """

    items: list[IngestItem] = []
    analyze_images: bool = False
    models: Optional[dict] = None
    domain_key: str = ""


class DismissRequest(BaseModel):
    """見送り / 復帰（``revoked`` 遷移。行は削除しない — P4 / PD5）。"""

    domain_key: str
    arxiv_id: str


# ---------------------------------------------------------------------------
# 購読
# ---------------------------------------------------------------------------


@router.get("/subscriptions")
def list_subscriptions(current_user: dict = Depends(_require_teacher)) -> dict:
    """分野購読の一覧を返す（TEACHER 以上）。購読は分野単位の共同財。"""
    session = _pg_session()
    try:
        subscriptions = pd_store.list_subscriptions(session)
    finally:
        session.close()
    return {"subscriptions": subscriptions}


@router.put("/subscriptions/{domain_key}")
def update_subscription(
    domain_key: str,
    body: SubscriptionUpdateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """購読条件を作成・更新する（教員の明示保存だけが購読を書き換える — PD3）。"""
    session = _pg_session()
    try:
        try:
            previous = pd_store.get_subscription(session, domain_key)
            stored = pd_store.upsert_subscription(
                session,
                domain_key,
                arxiv_categories=body.arxiv_categories,
                keyphrases=body.keyphrases,
                followed_authors=body.followed_authors,
                updated_by=current_user["id"],
            )
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=f"購読条件を保存できません: {exc}") from exc
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    record_review_event(
        AUDIT_ENTITY_PAPER_DISCOVERY,
        stored["domain_key"],
        _STATUS_SUBSCRIBED if previous else _STATUS_NONE,
        _STATUS_SUBSCRIBED,
        current_user["id"],
        {
            "action": "subscribe",
            "arxiv_categories": stored.get("arxiv_categories") or [],
            "keyphrases": [
                phrase.get("text", "") for phrase in (stored.get("keyphrases") or [])
            ],
            "followed_authors": stored.get("followed_authors") or [],
        },
    )
    return {"subscription": stored}


@router.get("/subscriptions/{domain_key}/keyphrase-candidates")
def list_keyphrase_candidates(
    domain_key: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """分野語彙から供給されるキーフレーズ候補を返す（出所付き — PD3）。

    購読行には**書かない**。採否は教員が編集 UI で決め、保存は
    ``PUT /subscriptions/{domain_key}`` だけが行う。
    """
    session = _pg_session()
    try:
        candidates = pd_vocab.keyphrase_candidates(session, domain_key)
    finally:
        session.close()
    return {"candidates": candidates}


# ---------------------------------------------------------------------------
# 検索（候補は保存しない — PD5）
# ---------------------------------------------------------------------------


@router.post("/search")
def search_candidates(
    body: SearchRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """購読条件で arXiv を検索し、注釈付きの候補一覧を返す。

    副作用は ``last_checked_at`` の更新のみ。arXiv API の失敗は **502 + 事実文**で、
    空一覧を「該当なし」と偽らない（PD6）。
    """
    requested = body.max_results if body.max_results is not None else DEFAULT_SEARCH_RESULTS
    max_results = max(1, min(MAX_SEARCH_RESULTS, int(requested)))
    start = max(0, int(body.start if body.start is not None else 0))

    session = _pg_session()
    try:
        try:
            result = pd_search.run_search(
                session,
                body.domain_key,
                categories=body.categories,
                keyphrases=body.keyphrases,
                followed_authors=body.followed_authors,
                start=start,
                max_results=max_results,
            )
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=f"検索条件が正しくありません: {exc}") from exc
        except arxiv_client.ArxivApiError as exc:
            session.rollback()
            logger.info("arXiv search failed for user=%s: %s", current_user["id"], exc)
            raise HTTPException(status_code=502, detail=_DETAIL_ARXIV_UNAVAILABLE) from exc
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return result


# ---------------------------------------------------------------------------
# 取り込み（PD1 の要 — 人間の弁）
# ---------------------------------------------------------------------------


@router.post("/ingest", status_code=202)
def ingest_candidates(
    body: IngestRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """選択された候補を取得し、既存のアップロードパイプラインへ流す。

    取得は**リクエスト内同期・少数件**（v1）。1件ごとの失敗は全体を落とさず
    ``failed`` に事実文で積む（部分成功をそのまま返す）。ただし
    ``NoDomainsConfiguredError``（許可ドメインが1件も無い）は**どの item も
    成功し得ない**ので、リクエスト全体を 422 にする。

    ``documents.source_url`` に PDF の取得 URL を保存し、次回以降の「取り込み済み」
    判定を読み時導出できるようにする（PD5）。
    """
    items = list(body.items or [])
    if not items:
        raise HTTPException(status_code=422, detail=_DETAIL_INGEST_EMPTY)
    if len(items) > MAX_INGEST_PER_REQUEST:
        raise HTTPException(status_code=422, detail=_DETAIL_INGEST_TOO_MANY)

    models_option: dict | None = None
    if body.models:
        models_option = _validate_models_option(body.models)

    session = _pg_session()
    try:
        allowed = [row["domain"] for row in url_fetch.list_url_fetch_domains(session)]
    finally:
        session.close()

    accepted: list[dict] = []
    failed: list[dict] = []

    for item in items:
        raw_id = str(getattr(item, "arxiv_id", "") or "")
        arxiv_id = pd_schema.normalize_arxiv_id(raw_id)
        if not arxiv_id:
            failed.append({"arxiv_id": raw_id, "detail": _DETAIL_INVALID_ARXIV_ID})
            continue

        source_url = pd_schema.pdf_url_for(arxiv_id)
        try:
            fetched = url_fetch.fetch_source_from_url(source_url, allowed)
        except url_fetch.NoDomainsConfiguredError as exc:
            # 許可リストが空 = どの item も成功し得ない。個別の失敗として黙らせず、
            # 設定が必要な事実を全体のエラーとして返す（fail-closed / UF1）。
            # ステータスは既存の写像を再利用する（写像を二重管理しない）。
            status = _URL_FETCH_ERROR_STATUS.get(type(exc), 422)
            logger.info(
                "arXiv ingest blocked (no allowed domains) for user=%s", current_user["id"],
            )
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        except url_fetch.UrlFetchError as exc:
            # 1件分の失敗は HTTPException にしない（残りの取り込みを止めない）。
            # 事実文はサーバの文言をそのまま渡す（独自文で上書きしない — UF6）。
            logger.info(
                "arXiv ingest fetch rejected (%s) for user=%s: %s",
                type(exc).__name__, current_user["id"], exc,
            )
            failed.append({"arxiv_id": arxiv_id, "detail": str(exc)})
            continue

        try:
            result = _accept_material_source(
                source_bytes=fetched.content,
                filename=fetched.filename,
                source_kind=fetched.source_kind,
                analyze_images=body.analyze_images,
                models_option=models_option,
                current_user=current_user,
                source_url=source_url,
            )
        except HTTPException as exc:
            # 保存・受理の失敗も1件分に閉じる（残りの取り込みを巻き添えにしない）。
            logger.warning(
                "arXiv ingest acceptance failed for %s (user=%s): %s",
                arxiv_id, current_user["id"], exc.detail,
            )
            failed.append({"arxiv_id": arxiv_id, "detail": str(exc.detail)})
            continue

        payload = dict(result or {})
        payload["arxiv_id"] = arxiv_id
        accepted.append(payload)

    record_review_event(
        AUDIT_ENTITY_PAPER_DISCOVERY,
        str(body.domain_key or "").strip() or _ENTITY_ID_FALLBACK,
        _STATUS_CANDIDATE,
        _STATUS_INGEST_REQUESTED,
        current_user["id"],
        {
            "action": "ingest",
            "arxiv_ids": [entry.get("arxiv_id", "") for entry in accepted],
            "failed_arxiv_ids": [entry.get("arxiv_id", "") for entry in failed],
            "accepted": len(accepted),
            "failed": len(failed),
        },
    )
    logger.info(
        "arXiv ingest requested by user=%s accepted=%s failed=%s",
        current_user["id"], len(accepted), len(failed),
    )
    return {"accepted": accepted, "failed": failed}


# ---------------------------------------------------------------------------
# 見送り / 復帰（行削除しない — P4 / PD5）
# ---------------------------------------------------------------------------


def _dismissal_op(body: DismissRequest, current_user: dict, *, restore: bool) -> dict:
    session = _pg_session()
    try:
        try:
            if restore:
                changed: Any = pd_store.restore(
                    session, body.domain_key, body.arxiv_id, current_user["id"]
                )
            else:
                changed = pd_store.dismiss(
                    session, body.domain_key, body.arxiv_id, current_user["id"]
                )
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=f"指定が正しくありません: {exc}") from exc
        if restore and changed is None:
            session.rollback()
            raise HTTPException(status_code=404, detail=_DETAIL_DISMISSAL_NOT_FOUND)
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    record_review_event(
        AUDIT_ENTITY_PAPER_DISCOVERY,
        changed["domain_key"],
        _STATUS_DISMISSED if restore else _STATUS_CANDIDATE,
        _STATUS_CANDIDATE if restore else _STATUS_DISMISSED,
        current_user["id"],
        {
            "action": "restore" if restore else "dismiss",
            "arxiv_id": changed["arxiv_id"],
        },
    )
    return changed


@router.post("/dismiss")
def dismiss_candidate(
    body: DismissRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """候補を見送る（``revoked=FALSE`` で記録。行削除しない）。"""
    return _dismissal_op(body, current_user, restore=False)


@router.post("/restore")
def restore_candidate(
    body: DismissRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """見送りを取り消す（``revoked=TRUE`` 遷移）。記録が無ければ 404。"""
    return _dismissal_op(body, current_user, restore=True)
