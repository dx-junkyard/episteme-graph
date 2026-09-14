"""論文ディスカバリー層 — 管理 API（実パス ``/api/admin/discovery/...``）。

正本: ``docs/features/paper_discovery_design.md`` §4.3（API 契約）/ §4.5（監査）/
§5（Phase 2 = バッチ取り込みと事前見積り）。
不変条項 PD1〜PD8 のうち、本ルータが構造として守るもの:

- **PD1 発見は自動、取り込みは教員の明示承認のみ**: 取り込みの入口は教員のリクエスト
  だけで、検索の副作用やスケジューラから取り込みが起動する経路を作らない。
  同期の ``POST /ingest`` は1リクエスト :data:`MAX_INGEST_PER_REQUEST` 件まで、
  Phase 2 の ``POST /ingest-batch``（キューへ積むだけ）は
  :data:`MAX_INGEST_BATCH` 件まで。キュー行を作れるのは ``/ingest-batch`` と
  ``/ingest-queue/{id}/retry`` の2本だけで、``api/ingest_worker.py`` の worker は
  積まれた行を処理するだけ（自分で候補を作らない・arXiv を検索しない）。
- **PD2 取得・解析は既存経路へ完全合流**: 論文の取得は
  ``core.url_fetch.fetch_source_from_url``（許可リスト照合・SSRF ガード・形式判定）に
  一任し、受理後は ``routes.admin._accept_material_source`` へ合流する。
  **このモジュールは HTTP クライアント（requests / httpx 等）を import しない**。
- **PD3 購読は教員の意思の正本**: サーバが購読条件を書き換えるのは
  ``PUT /subscriptions/{domain_key}``（教員の明示保存）だけ。
  ``/keyphrase-candidates`` は候補を返すだけで購読行に書かない。
- **PD4 数値スコアを見せない**: core の DTO をそのまま返し、類似度・一致度の
  生数値を足さない（``core.paper_discovery.search`` が構造として持たない）。
  Phase 3 の関連度ランキングも、この層が受け取るのは並び順と段階ラベル
  （``relevance_label``）だけで、cosine の生値は ``core.paper_discovery.ranking``
  の内側から出てこない。
- **PD5 候補は保存せず読み時導出**: 保存するのは購読条件・見送り記録・取り込み出所
  （``documents.source_url``）だけ。候補一覧のスナップショットを作らない。
- **PD6 閉世界の正直さ**: 検索は core の DTO（``query`` / ``closed_world_note``
  同梱）を素通しし、arXiv API 失敗を空一覧に化けさせない（502 + 事実文）。

論文レーダー（``docs/features/paper_radar_design.md`` / PR1〜PR8）の4本
（``/radar/seed`` / ``/radar/search`` / ``/radar/compare`` / ``/radar/provenance``）も
同じルータに足す。本ルータが構造として守るのは:

- **PR3 取り込みは既存の弁のみ**: レーダーは候補提示までで、専用の取得・取り込み
  エンドポイントを持たない（教員は既存の ``/ingest`` / ``/ingest-batch`` を叩く）。
  ``/radar/provenance`` は**論文を取得しない** — 既にある教材の出所
  （``documents.source_url``）を後から記帳するだけで、取得・解析経路には触れない。
- **PR5 教員の明示操作のみ**: 探索の3本は読み取り専用で、購読・見送りへ書き込まない
  （``last_checked_at`` も更新しない）。監査も記帳しない（``/search`` と同じ扱い）。
  唯一の書き込みは ``/radar/provenance``（教員の明示操作 + 監査記帳 + edit 権限）。
- **PR8 教員専用 + document 可視性ゲート**: ``_radar_document_or_404`` が
  ``services.resolve_document_access`` で view を確認し、不可視と不在を同一 404 に
  する（``routes/landscape.py`` と同じ fail-closed の作法）。学習者向けの
  レーダー系ルートは作らない。

コーパスを補う論文（``docs/features/corpus_complement_design.md`` / CC1〜CC8）の2本
（``/complement/search`` / ``/complement/foundation``）も同じルータに足す。本ルータが
構造として守るのは:

- **CC2 決定論・非LLM**: 補完の2本は LLM を呼ばない。レンズ A/B の埋め込みは既存の
  関連度バッチ（``ranking.rank_candidates`` の1コール）へ相乗りし、発見層の
  ``core.llm`` 接触点を増やさない。
- **CC3 保存は外部事実のキャッシュだけ**: 候補・レンズ判定は保存しない（PD5）。
  ``/complement/foundation`` の書き込みは参照リストキャッシュ（migration 077）の
  upsert のみで、教員の判断を含まないため**監査記帳もしない**。
- **CC7 取り込みは既存の弁のみ**: 補完も候補提示までで、専用の取得・取り込み
  エンドポイントを持たない（既存の ``/ingest`` / ``/ingest-batch`` をそのまま使う）。
- **CC8 fail-soft**: レンズが成立しなくても検索は 200 で成立させ、縮退した
  そのレンズだけを ``available:false`` + 事実文で正直に返す。

エラーは日本語の事実文で、内部情報（解決 IP・スタックトレース）を ``detail`` に
載せない（UF6 継承）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import services
from dependencies import ROLE_SYSTEM_ADMIN, _require_teacher
from services import aggregate_frontier_interest, record_review_event

from core import decision_context
from core import url_fetch
from core.config import get_settings
from core.llm_usage import metrics as usage_metrics
from core.llm_worker.cost_gate import CostGate, today_str
from core.paper_discovery import arxiv_client
from core.paper_discovery import citation_client as pd_citation_client
from core.paper_discovery import citation_search as pd_citation_search
from core.paper_discovery import compare as pd_compare
from core.paper_discovery import complement as pd_complement
from core.paper_discovery import foundation as pd_foundation
from core.paper_discovery import ingest_queue as pd_queue
from core.paper_discovery import radar as pd_radar
from core.paper_discovery import ranking as pd_ranking
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

#: 1リクエストでキューへ登録できる論文の上限（Phase 2 のバッチ投入）。
MAX_INGEST_BATCH = 50

#: 取り込みキュー一覧の既定件数。
DEFAULT_QUEUE_LIMIT = 50

#: 事前見積りで一度に見積れる件数の上限（画面の「N件ぶん」の N）。
MAX_ESTIMATE_ITEMS = 200

#: ``POST /search`` の 1 リクエスト取得件数の上限（arXiv への行儀 — PD7）。
MAX_SEARCH_RESULTS = 100

#: 検索結果の既定件数。
DEFAULT_SEARCH_RESULTS = 50

#: ``POST /search`` の並び順（Phase 3）。既定は従来どおり新着順で、関連度は明示指定のみ。
ORDER_DATE = "date"
ORDER_RELEVANCE = "relevance"
SEARCH_ORDERS = (ORDER_DATE, ORDER_RELEVANCE)

_DETAIL_INGEST_EMPTY = "取り込む論文が選択されていません。"
_DETAIL_INGEST_TOO_MANY = (
    f"一度に取り込めるのは{MAX_INGEST_PER_REQUEST}件までです。"
    "件数を減らして実行してください。"
)
_DETAIL_INVALID_ARXIV_ID = "arXiv ID として解釈できませんでした。"
_DETAIL_ARXIV_UNAVAILABLE = (
    "arXiv に接続できませんでした。時間をおいて再度お試しください。"
)
#: arXiv からアクセスを制限された（HTTP 429）。接続不能と同じ文言にしない — 設定・
#: 回線を疑わせずに「制限されている」という事実を言うため（PR7 / PD6 の事実文の流儀）。
#: **文言の正本は core 側**（``radar.NOTE_ARXIV_BLOCKED``）— 同じ事実を route で
#: 言い換えない（2026-09-15 / 設計書 §14.7）。この応答を返した時点でクールダウンが
#: 立っているので、「しばらく問い合わせを止めている」は事実として正しい。
_DETAIL_ARXIV_RATE_LIMITED = pd_radar.NOTE_ARXIV_BLOCKED
_DETAIL_DISMISSAL_NOT_FOUND = "この見送り記録は見つかりません。"
_DETAIL_BATCH_TOO_MANY = (
    f"一度にキューへ登録できるのは{MAX_INGEST_BATCH}件までです。"
    "件数を減らして実行してください。"
)
_DETAIL_RETRY_NOT_FAILED = "再試行できるのは失敗した項目だけです。"
_DETAIL_INVALID_ORDER = "並び順の指定が正しくありません。"
_DETAIL_CITATION_UNAVAILABLE = (
    "引用グラフの照会に接続できませんでした。時間をおいて再度お試しください。"
)

# ── 論文レーダー（正本 docs/features/paper_radar_design.md §5.5）─────────────
#: 不可視と不在を区別しない（存在を知れる 403 を使わない — PR8 / landscape.py と同文）。
_DETAIL_DOCUMENT_NOT_FOUND = "Document not found"
_DETAIL_INVALID_DISTANCE = "距離の指定が正しくありません。"
_DETAIL_COMPARE_EMPTY = "比較する候補が選択されていません。"
_DETAIL_COMPARE_TOO_MANY = (
    f"一度に比較できるのは{pd_compare.RADAR_COMPARE_MAX_CANDIDATES}件までです。"
    "件数を減らして実行してください。"
)
_DETAIL_COMPARE_LIMIT = (
    "本日の比較分析の上限に達しました。明日以降に再度お試しください。"
)
_DETAIL_COMPARE_UNAVAILABLE = (
    "比較分析を実行できませんでした。時間をおいて再度お試しください。"
)
#: arXiv 出所の後付け登録（3段階）の事実文。数値・内部情報を載せない。
_DETAIL_PROVENANCE_FORBIDDEN = "この教材の取得元を登録する権限がありません。"
_DETAIL_PROVENANCE_ALREADY = "この教材には、すでに取得元が登録されています。"
_DETAIL_PROVENANCE_MISMATCH = (
    "指定された arXiv ID は、この教材から推定された ID と一致しません。"
    "画面を再読み込みして、もう一度お試しください。"
)
_DETAIL_PROVENANCE_UNVERIFIED = (
    "arXiv から論文情報を取得できなかったため、取得元を登録できません。"
    "時間をおいて再度お試しください。"
)
_DETAIL_PROVENANCE_TITLE_MISMATCH = (
    "教材のタイトルと arXiv 論文のタイトルが一致しません。"
    "同じ論文であることを確認したうえで登録してください。"
)

#: 許可リストに arXiv の取得先が無いまま投入されたときの注記（黙って受理しない — PD6）。
_NOTICE_DOMAIN_NOT_ALLOWED = (
    "現在、取得先ドメインが許可されていないため、許可されるまで取り込みは失敗します。"
)

# 監査の status 語彙（``metadata.action`` で操作を区別する — 既存層と同じ流儀）。
_STATUS_NONE = ""
_STATUS_SUBSCRIBED = "subscribed"
_STATUS_CANDIDATE = "candidate"
_STATUS_DISMISSED = "dismissed"
_STATUS_INGEST_REQUESTED = "ingest_requested"
_STATUS_QUEUED = "queued"
_STATUS_FAILED = "failed"
_STATUS_PROVENANCE_REGISTERED = "provenance_registered"

#: 出所を記帳した根拠（監査 metadata の ``method``）。``auto_title_match`` = タイトルが
#: 正規化一致したので確認なしで記帳 / ``teacher_confirmed`` = 一致しないが教員が確定。
_PROVENANCE_METHOD_AUTO = "auto_title_match"
_PROVENANCE_METHOD_CONFIRMED = "teacher_confirmed"

#: 分野が特定できない操作（取り込みは複数分野にまたがり得る）の監査 entity_id。
_ENTITY_ID_FALLBACK = "arxiv"

#: 確定文脈（DC2）— 一括取り込みを覆せる実際の経路。キュー行の retry は「失敗の再試行」
#: であって取り込みの取り消しではないので、覆す経路は教材そのものの削除で書く
#: （その削除も監査される — 是正 F11）。
_INGEST_REOPEN_PATH = "DELETE /api/admin/materials/{material_id}"


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
    """候補検索。条件を渡すと保存済み購読より優先する（保存せず試せる — PD3）。

    ``order`` は Phase 3 の並べ替え指定（``date`` 既定 / ``relevance``）。既定のまま
    なら Phase 1〜2 と**完全に同一**のレスポンスを返す（後方互換）。
    """

    domain_key: str = ""
    categories: Optional[list] = None
    keyphrases: Optional[list] = None
    followed_authors: Optional[list] = None
    start: int = 0
    max_results: int = DEFAULT_SEARCH_RESULTS
    order: str = ORDER_DATE


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


class IngestBatchItem(BaseModel):
    """キューへ積む1件。``title`` は候補カードから引き継ぐ表示用（任意）。"""

    arxiv_id: str
    title: str = ""


class IngestBatchRequest(BaseModel):
    """バッチ取り込み（Phase 2）。積むのは教員の明示操作だけ（PD1）。"""

    items: list[IngestBatchItem] = []
    domain_key: str = ""
    analyze_images: bool = False
    models: Optional[dict] = None


class CitationSearchRequest(BaseModel):
    """引用グラフからの候補供給（Phase 3）。取り込みはしない — 候補提示のみ（PD1）。"""

    domain_key: str = ""


class DismissRequest(BaseModel):
    """見送り / 復帰（``revoked`` 遷移。行は削除しない — P4 / PD5）。"""

    domain_key: str
    arxiv_id: str


class RadarSearchRequest(BaseModel):
    """レーダー検索（教材起点）。条件を渡すと seed 由来の供給より優先する（PR1）。

    ``distance`` は ``near`` / ``mid`` / ``far``（語彙の正本は
    ``core.paper_discovery.schema.RADAR_DISTANCES``。語彙外は 422）。
    """

    document_ref: str = ""
    distance: str = "near"
    categories: Optional[list] = None
    keyphrases: Optional[list] = None
    start: int = 0
    max_results: int = DEFAULT_SEARCH_RESULTS


class RadarProvenanceRequest(BaseModel):
    """arXiv 出所の後付け登録（``documents.source_url`` への記帳）。

    ``confirm`` はタイトルが一致しないときの教員の明示確定。既定は ``False`` で、
    照合できない候補を黙って記帳しない（3段階目の弁）。
    """

    document_ref: str = ""
    arxiv_id: str = ""
    confirm: bool = False


class RadarCompareRequest(BaseModel):
    """比較分析（PR4 — 結果は保存しない一時的な注釈）。"""

    document_ref: str = ""
    arxiv_ids: list[str] = []


# ---------------------------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------------------------


def _arxiv_unavailable_detail(exc: Exception) -> str:
    """arXiv 到達失敗の事実文（アクセスの制限だけ別の文にする — PR7）。

    ``ArxivRateLimitedError`` は ``ArxivApiError`` の部分型なので、捕捉側は
    ``except arxiv_client.ArxivApiError`` のまま1本で受けて、文言だけをここで
    分ける（except 節を4箇所に増やさない）。ステータスは 502 のまま揃える —
    上流の制限を本アプリ自身のコスト上限（429）と同じ形にしない。
    """
    if isinstance(exc, arxiv_client.ArxivRateLimitedError):
        return _DETAIL_ARXIV_RATE_LIMITED
    return _DETAIL_ARXIV_UNAVAILABLE


def _arxiv_blocked() -> bool:
    """いま arXiv からアクセスを制限されている最中か（判定の正本は core — §14.7）。

    真なら検索・比較・購読検索は **arXiv を呼ばずに** 200 + ``arxiv_blocked: true`` +
    空候補 + 事実文で返す（呼んでも同じ結果で、ブロックの窓を人の操作で延ばすだけ）。
    ここで見るのは真偽だけ — 残り時間を返す関数は route から参照しない
    （数値を人に見せないため。PR2。ガードレールが識別子の不在で固定する）。
    """
    return pd_radar.arxiv_blocked()


# ---------------------------------------------------------------------------
# 購読
# ---------------------------------------------------------------------------


@router.get("/subscriptions")
def list_subscriptions(current_user: dict = Depends(_require_teacher)) -> dict:
    """分野購読の一覧を返す（TEACHER 以上）。購読は分野単位の共同財。

    ``citation_source_enabled`` は引用グラフ供給（Phase 3）のオプトイン状態。
    フロントのボタン活性判定に使う**補助**で、強制はサーバ側
    （``citation_search.run_citation_search`` が無効時に外部 API を呼ばない）。
    """
    session = _pg_session()
    try:
        subscriptions = pd_store.list_subscriptions(session)
    finally:
        session.close()
    return {
        "subscriptions": subscriptions,
        "citation_source_enabled": pd_citation_search.citation_source_enabled(),
    }


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


def _anchor_context(session, domain_key: str) -> Optional[dict]:
    """着地予測（VA層 §8）の材料。使えないときは ``None``（キーごと付かない）。

    骨格なし・アンカー未構築・DB 不達のいずれも**正常な状態**として静かに縮退する
    （VA4）。返すのは現行凍結版のアンカーとその版だけで、生スコアには触れない。
    """
    try:
        from core.atlas_vectors.builder import anchors_with_labels

        anchors, version = anchors_with_labels(session, domain_key)
    except Exception:  # noqa: BLE001 — 着地予測が出ないだけ（検索は成立させる）
        logger.warning(
            "landing prediction unavailable for domain %s (non-fatal)",
            domain_key, exc_info=True,
        )
        return None
    if not anchors or not version:
        return None
    return {"anchors": anchors, "skeleton_version": version}


def _apply_relevance_order(session, domain_key: str, result: dict) -> dict:
    """検索結果を関連度で並べ替える（Phase 3）。**失敗しても検索を落とさない**。

    生の類似度は ``ranking`` 側で閉じており、ここが受け取るのは並び順と段階ラベル
    だけ（PD4）。並べ替え不能は ``ranking.available=false`` + 事実文で正直に返し、
    候補は新着順のまま残す（PD6 — 黙って空にしない・黙って順序を変えない）。

    現行凍結版のアンカーが読めるときは、同じ経路で着地予測（VA層 §8 の ``landing``）も
    付く。アンカーが無ければキー自体が付かない（既存レスポンスと後方互換）。
    """
    candidates = list(result.get("candidates") or [])
    try:
        ranked = pd_ranking.rank_candidates(
            session,
            domain_key,
            candidates,
            anchor_context=_anchor_context(session, domain_key),
        )
    except Exception:  # noqa: BLE001 — 並べ替えの失敗で検索結果を捨てない
        logger.warning("relevance ordering failed for domain %s", domain_key, exc_info=True)
        ranked = {"available": False, "note": pd_ranking.NOTE_UNAVAILABLE, "ordered": candidates}

    payload = dict(result)
    payload["order"] = ORDER_RELEVANCE
    payload["candidates"] = list(ranked.get("ordered") or candidates)
    ranking_info: dict[str, Any] = {"available": bool(ranked.get("available"))}
    note = ranked.get("note")
    if note:
        ranking_info["note"] = note
    payload["ranking"] = ranking_info
    return payload


@router.post("/search")
def search_candidates(
    body: SearchRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """購読条件で arXiv を検索し、注釈付きの候補一覧を返す。

    副作用は ``last_checked_at`` の更新のみ。arXiv API の失敗は **502 + 事実文**で、
    空一覧を「該当なし」と偽らない（PD6）。

    ``order="relevance"``（Phase 3）のときだけ、分野コーパスの重心との関連度で
    並べ替え、各候補に段階ラベル ``relevance_label`` を付ける（生スコアは返さない
    — PD4）。並べ替えができないとき（コーパス無し・埋め込み失敗・日次上限）は
    ``ranking.available=false`` + 事実文で**新着順のまま**返す（検索は必ず成立させる）。
    既定の ``order="date"`` では ``ranking`` キー自体を付けない（後方互換）。

    arXiv からアクセスを制限されている（クールダウン中）と**呼ぶ前から分かっている**
    ときは、arXiv を呼ばずに 200 + ``arxiv_blocked=true`` + 空候補 + 事実文で返す
    （``last_checked_at`` / ``last_search_found_new`` も更新しない — 探していないので
    「確かめた」と記録しない。設計書 §14.7）。live の 429 は従来どおり 502。
    """
    requested = body.max_results if body.max_results is not None else DEFAULT_SEARCH_RESULTS
    max_results = max(1, min(MAX_SEARCH_RESULTS, int(requested)))
    start = max(0, int(body.start if body.start is not None else 0))
    order = str(body.order or ORDER_DATE).strip()
    if order not in SEARCH_ORDERS:
        raise HTTPException(status_code=422, detail=_DETAIL_INVALID_ORDER)

    blocked_note = pd_radar.NOTE_ARXIV_BLOCKED if _arxiv_blocked() else ""

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
                arxiv_blocked_note=blocked_note,
            )
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=f"検索条件が正しくありません: {exc}") from exc
        except arxiv_client.ArxivApiError as exc:
            session.rollback()
            logger.info("arXiv search failed for user=%s: %s", current_user["id"], exc)
            raise HTTPException(status_code=502, detail=_arxiv_unavailable_detail(exc)) from exc
        session.commit()
        if order == ORDER_RELEVANCE and not blocked_note:
            # 候補ゼロを並べ替えるために embedding を焚かない（制限中は関連度の
            # 材料そのものが無い）。並び順の指定は受理したまま黙って無視する。
            result = _apply_relevance_order(session, body.domain_key, result)
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    # ``arxiv_blocked`` は core の DTO に**常に**入っている（§14.7）。route では足さない
    # — この経路は core の DTO を素通しするのが約束（PD4 / test_paper_discovery_ranking）。
    return result


@router.post("/citation-search")
def citation_search_candidates(
    body: CitationSearchRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """取り込み済み論文の引用・推薦関係から候補を導出する（Phase 3 / 設計書 §6）。

    **候補提示のみ**で取り込みはしない（PD1 — 取り込みは既存の ``/ingest`` /
    ``/ingest-batch`` を教員が明示的に叩く）。候補は保存しない（PD5）ため副作用ゼロで、
    監査も記帳しない（``/search`` と同じ扱い）。

    オプトイン（``DISCOVERY_CITATION_SOURCE_ENABLED``）が無効なときは 403 / 404 に
    せず ``{"enabled": false, "note": ...}`` を返す（機能の存在は隠さない）。
    外部 API の失敗は **502 + 事実文**で、空一覧を「該当なし」と偽らない（PD6）。
    """
    session = _pg_session()
    try:
        try:
            return pd_citation_search.run_citation_search(session, body.domain_key)
        except pd_citation_client.CitationApiError as exc:
            logger.info(
                "citation search failed for user=%s: %s", current_user["id"], exc
            )
            raise HTTPException(
                status_code=502, detail=_DETAIL_CITATION_UNAVAILABLE
            ) from exc
    finally:
        session.close()


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
# バッチ取り込み（Phase 2 — キューに積むのは教員の明示操作だけ、PD1）
# ---------------------------------------------------------------------------


def _arxiv_fetch_allowed(session) -> bool:
    """arXiv の配信ホストが現在の許可リストで取得可能か（UI の無効化は補助 — UF1）。"""
    try:
        domains = [row["domain"] for row in url_fetch.list_url_fetch_domains(session)]
    except Exception:  # noqa: BLE001 — 判定不能を「許可済み」に化けさせない
        logger.warning("failed to read url fetch domains for ingest batch notice", exc_info=True)
        return False
    return url_fetch.domain_allowed(pd_schema.ARXIV_SITE_HOST, domains)


@router.post("/ingest-batch", status_code=202)
def ingest_batch(
    body: IngestBatchRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """選択された候補を取り込みキューへ積む（実際の取得は非同期 worker が行う）。

    ここで積まれた行は「教員が承認した」という事実そのもの（PD1）。検索や worker が
    行を作る経路は無い。積まなかった候補は ``skipped`` に事実文つきで返す
    （黙って落とさない）。

    ``models`` は**この時点で** ``_validate_models_option`` により fail-closed 検証する
    （worker は再検証しない — 検証の正本を1箇所に保つ）。

    許可リストに arXiv の取得先が無くても**受理はする**（許可が後から入れば流れる）。
    ただし現状では失敗することを ``notice`` に事実文で添える（PD6 — 黙らない）。
    """
    items = list(body.items or [])
    if not items:
        raise HTTPException(status_code=422, detail=_DETAIL_INGEST_EMPTY)
    if len(items) > MAX_INGEST_BATCH:
        raise HTTPException(status_code=422, detail=_DETAIL_BATCH_TOO_MANY)

    models_option: dict | None = None
    if body.models:
        models_option = _validate_models_option(body.models)

    session = _pg_session()
    try:
        try:
            result = pd_queue.enqueue_items(
                session,
                [{"arxiv_id": item.arxiv_id, "title": item.title} for item in items],
                domain_key=body.domain_key,
                requested_by=current_user["id"],
                analyze_images=body.analyze_images,
                models=models_option,
            )
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=f"指定が正しくありません: {exc}") from exc
        session.commit()
        fetch_allowed = _arxiv_fetch_allowed(session)
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    payload: dict = {"queued": result["queued"], "skipped": result["skipped"]}
    if result["queued"] and not fetch_allowed:
        payload["notice"] = _NOTICE_DOMAIN_NOT_ALLOWED

    # 改訂原則1（DC1）: これは「選択した N 件を取り込む」型の一括確定で、取り込みの弁は
    # 教員だけが持つ（PD1）。提示集合はリクエストの候補集合そのもの（説明レビューキューの
    # 一括承認と同じ理由 — 教員が画面で選んだ集合が提示集合である）で、適用集合は実際に
    # キューへ積まれた行。積まなかった候補は ``skipped`` に残る（黙って落とさない）。
    ctx = decision_context.build_decision_context(
        basis=decision_context.BASIS_DISCOVERY_INGEST_BATCH,
        presented_ids=[item.arxiv_id for item in items],
        applied_ids=[entry["arxiv_id"] for entry in result["queued"]],
        # 候補行のチェックボックスは外せる（一括の対象にしない）し、行ごとに「見送る」
        # （`/dismiss`。行削除ではなく status 遷移で保持）を選べる。
        alternatives=(
            decision_context.ALT_DESELECT,
            decision_context.ALT_DISMISS,
        ),
        # 取り込んだ結果は教材として削除で取り消せる（その削除も記帳される — 是正 F11）。
        # キュー行の status（queued/failed 等）は「戻せる status」ではないので空のまま。
        reopen_path=_INGEST_REOPEN_PATH,
        evidence_shown=None,
    )
    record_review_event(
        AUDIT_ENTITY_PAPER_DISCOVERY,
        str(body.domain_key or "").strip() or _ENTITY_ID_FALLBACK,
        _STATUS_CANDIDATE,
        _STATUS_QUEUED,
        current_user["id"],
        decision_context.attach_decision_context(
            {
                "action": "ingest_batch",
                "arxiv_ids": [entry["arxiv_id"] for entry in result["queued"]],
                "skipped_arxiv_ids": [entry["arxiv_id"] for entry in result["skipped"]],
                "queued": len(result["queued"]),
                "skipped": len(result["skipped"]),
                "bulk": True,
            },
            ctx,
        ),
    )
    logger.info(
        "arXiv ingest batch queued by user=%s queued=%s skipped=%s",
        current_user["id"], len(result["queued"]), len(result["skipped"]),
    )
    return payload


@router.get("/ingest-queue")
def list_ingest_queue(
    domain_key: str = Query(default=""),
    limit: int = Query(default=DEFAULT_QUEUE_LIMIT),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """取り込みキューを新しい順に返す（失敗行も ``detail`` つきで残る — P4）。"""
    session = _pg_session()
    try:
        items = pd_queue.list_items(session, domain_key=domain_key, limit=limit)
    finally:
        session.close()
    return {"items": items}


@router.post("/ingest-queue/{item_id}/retry")
def retry_ingest_item(
    item_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """失敗した項目を ``queued`` へ戻す（教員の明示操作のみ — P4 / PD1）。

    ``failed`` 以外（処理中・受理済み・存在しない）は 422。前回の ``detail`` は
    消さずに残す。
    """
    session = _pg_session()
    try:
        changed = pd_queue.retry_item(session, item_id)
        if changed is None:
            session.rollback()
            raise HTTPException(status_code=422, detail=_DETAIL_RETRY_NOT_FAILED)
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
        changed["domain_key"] or _ENTITY_ID_FALLBACK,
        _STATUS_FAILED,
        _STATUS_QUEUED,
        current_user["id"],
        {"action": "ingest_retry", "arxiv_id": changed["arxiv_id"], "item_id": changed["item_id"]},
    )
    return {"item": changed}


@router.get("/ingest-estimate")
def get_ingest_estimate(
    count: int = Query(default=1),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """取り込み確認画面に出す事前見積り（設計書 §5）。

    U層の流儀をそのまま踏襲する: 実測（reported）と推計（estimated）は**分離**して
    返し（U1）、**レンジのみ・金額なし**（U5）。実績が1件も無ければ捏造せず
    ``available: false`` + 事実文を返す。

    導出は読み取りのみ（``llm_usage_events`` は append-only 台帳 — U6）。
    """
    item_count = max(0, min(MAX_ESTIMATE_ITEMS, int(count or 0)))
    session = _pg_session()
    try:
        return usage_metrics.recent_document_run_estimate(session, item_count=item_count)
    finally:
        session.close()


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


# ---------------------------------------------------------------------------
# 学習者の関心（コーパス回遊層 Phase D）
# 正本: docs/features/corpus_roaming_design.md §7（CR6 / CR10）。
# ---------------------------------------------------------------------------


@router.get("/frontier-interest")
def list_frontier_interest(
    domain_key: str = Query(""),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """地図の端への学習者の関心（分野×領域×輪の **k-匿名レンジ**のみ）。

    - 集計・閾値は ``services.aggregate_frontier_interest``（``core/privacy.py`` の
      k=3 / レンジ 3-5・6-10・11+ に委譲）。``n < k`` の行は返さない。
    - **個人・時系列・順位・生の件数を返さない**（CR6 / CR3）。ここに出るのは需要の
      提示であって、購読条件・取り込み・骨格を自動変更する入力ではない（CR10）。
    - 新しいダッシュボードを作らず、既存のディスカバリーモーダルの中で1行として
      読む前提の最小 DTO（``{domain_key, region_id, ring, range_label}``）。
    """
    rows = aggregate_frontier_interest((domain_key or "").strip() or None)
    return {"rows": rows}


# ---------------------------------------------------------------------------
# 論文レーダー（教材起点の探索・比較分析）
# 正本: docs/features/paper_radar_design.md §5.5（PR1〜PR8）。
# すべて読み取り専用 — 購読・見送り・キューへ書かず、監査も記帳しない。
# ---------------------------------------------------------------------------

#: 比較分析の日次上限（PR: 教員ごと・day-only。``figure_suggest`` と同型の in-memory
#: ゲートで、プロセスローカルである制約は既存踏襲）。
_radar_compare_gate = CostGate()


def _radar_document_or_404(document_ref: str, current_user: dict):
    """``document_ref``（UUID / material_id）を解決し **view** をゲートする（PR8）。

    不在・権限なしはどちらも **404**（``routes/landscape.py::_document_access_or_404``
    と同じ fail-closed の作法。403 は「存在は知れる」ため使わない）。レーダーは
    読み取りのみなので edit は要求しない。
    """
    access = services.resolve_document_access(current_user.get("id"), document_ref)
    if not access.found:
        raise HTTPException(status_code=404, detail=_DETAIL_DOCUMENT_NOT_FOUND)
    if str(current_user.get("role") or "") == ROLE_SYSTEM_ADMIN:
        return access
    if not access.can_view:
        raise HTTPException(status_code=404, detail=_DETAIL_DOCUMENT_NOT_FOUND)
    return access


def _radar_can_register(access, current_user: dict) -> bool:
    """``documents.source_url`` を記帳できる立場か（SYSTEM_ADMIN は edit を免除）。

    権限は core（``radar.resolve_seed``）が知らない情報なので、DTO への注入は
    route 層の責務（core が user を知らない構造を維持する）。
    """
    if str(current_user.get("role") or "") == ROLE_SYSTEM_ADMIN:
        return True
    return bool(getattr(access, "can_edit", False))


def _with_can_register(seed: dict, access, current_user: dict) -> dict:
    """seed DTO の ``provenance`` に ``can_register`` を注入して返す。

    フロントはこの値が偽なら登録導線を出さない（**表示の補助**で、強制はサーバ側の
    ``POST /radar/provenance`` の権限ゲート）。
    """
    payload = dict(seed or {})
    provenance = dict(payload.get("provenance") or {})
    provenance["can_register"] = _radar_can_register(access, current_user)
    payload["provenance"] = provenance
    return payload


def _merge_fetched_metadata(seed: dict, fetched: dict) -> dict:
    """記帳後に導出し直した seed へ、**同じ操作の中で1度だけ引いた** arXiv 由来の
    メタデータ（要旨・カテゴリ）を移す。

    出所を記帳したあとの seed は ``provenance.status="registered"`` になるが、
    2度目の導出は arXiv を呼ばない（``fetch_arxiv=False``）ので要旨・カテゴリが
    空になる。ここで1度目の結果を移すことで、教員の1操作あたりの arXiv 呼び出しを
    1回に保ったまま、応答の中身は従来（2回引いていた頃）と同じにする（設計書 §14）。

    移すのは arXiv 由来と分かっているときだけで（``categories_source`` が
    ``arxiv`` / ``arxiv_inferred``）、購読・手入力由来の条件は上書きしない。
    記帳済みなので供給元は ``arxiv``（推定ではなくなった）に揃える。
    """
    payload = dict(seed or {})
    source = str((fetched or {}).get("categories_source") or "")
    if source not in (
        pd_radar.CATEGORIES_SOURCE_ARXIV,
        pd_radar.CATEGORIES_SOURCE_ARXIV_INFERRED,
    ):
        return payload
    if not payload.get("summary") and fetched.get("summary"):
        payload["summary"] = fetched["summary"]
    if fetched.get("categories"):
        payload["categories"] = list(fetched["categories"])
        payload["categories_source"] = pd_radar.CATEGORIES_SOURCE_ARXIV
    if fetched.get("metadata_source"):
        payload["metadata_source"] = fetched["metadata_source"]
    return payload


def _persist_arxiv_metadata_cache(session) -> None:
    """arXiv メタデータの写し（migration 085）だけを確定させる。

    レーダーの探索経路は読み取り専用だが、``core`` が read-through で残した**外部
    事実の写し**（候補でも教員の判断でもない — 設計書 §14 / CC3 同型）はここで
    ``commit`` しないと閉じたセッションと一緒に捨てられ、画面を開き直すたびに
    arXiv を呼ぶことになる。確定できなくても探索の結果は返す（fail-soft）。
    """
    try:
        session.commit()
    except Exception:  # noqa: BLE001 — キャッシュの確定失敗で応答を落とさない
        logger.warning("arXiv metadata cache commit failed", exc_info=True)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("rollback after cache commit failure failed", exc_info=True)


def _consume_radar_compare_quota(user_id: str) -> None:
    """比較分析の日次上限を1消費する。超過は 429 + 事実文（数値を返さない）。"""
    cap = int(getattr(get_settings(), "discovery_compare_max_calls_per_day", 20) or 0)
    if not _radar_compare_gate.check_and_count(
        daily_limit=cap, daily_key=(today_str(), user_id)
    ):
        raise HTTPException(status_code=429, detail=_DETAIL_COMPARE_LIMIT)


@router.get("/radar/seed")
def get_radar_seed(
    document_ref: str = Query(default=""),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """レーダーの起点（seed）— 検索条件の供給元とメタデータを返す（PR1）。

    保存はしない（毎回導出）。arXiv 由来として登録されていない教材では
    ``arxiv_id`` / ``abs_url`` が ``null`` で ``categories_source="manual"``
    （判定不能を偽装しない — PD6）。ファイル名等から出所を**推定**できた場合は
    ``provenance.status="inferred"`` で、``categories_source="arxiv_inferred"``。
    推定の段階では ``documents`` へ何も書かない（記帳は
    ``POST /radar/provenance`` の明示操作だけ）。

    ``provenance.can_register`` は route 層で注入する権限フラグ（フロントの登録導線の
    出し分け用。強制は登録 API 側のゲート）。
    """
    access = _radar_document_or_404(document_ref, current_user)
    session = _pg_session()
    try:
        try:
            seed = pd_radar.resolve_seed(session, str(access.document_id or ""))
        except LookupError as exc:
            raise HTTPException(
                status_code=404, detail=_DETAIL_DOCUMENT_NOT_FOUND
            ) from exc
        # arXiv から引けた分の写しを残す（次に開いたときは呼ばない — 設計書 §14）。
        _persist_arxiv_metadata_cache(session)
    finally:
        session.close()
    return {"seed": _with_can_register(seed, access, current_user)}


@router.post("/radar/search")
def radar_search(
    body: RadarSearchRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """seed 教材の周辺を距離を選んで探す（候補提示のみ — 取り込みは既存経路、PR3）。

    副作用ゼロ（購読の ``last_checked_at`` も更新しない — PR5）。arXiv API の失敗は
    **502 + 事実文**で、空一覧を「該当なし」と偽らない（PD6 / PR7）。

    arXiv からアクセスを制限されている（クールダウン中）と**呼ぶ前から分かっている**
    ときだけは、例外にせず 200 + ``arxiv_blocked=true`` + 空候補 + 事実文を返す
    （:func:`pd_radar.blocked_radar_result`。live の 429 は従来どおり 502 —
    「探して断られた」と「呼ぶ前から止めている」を同じ形にしない。設計書 §14.7）。
    """
    distance = str(body.distance or "").strip()
    if distance not in pd_schema.RADAR_DISTANCES:
        raise HTTPException(status_code=422, detail=_DETAIL_INVALID_DISTANCE)

    requested = body.max_results if body.max_results is not None else DEFAULT_SEARCH_RESULTS
    max_results = max(1, min(MAX_SEARCH_RESULTS, int(requested)))
    start = max(0, int(body.start if body.start is not None else 0))

    access = _radar_document_or_404(body.document_ref, current_user)

    session = _pg_session()
    try:
        try:
            if _arxiv_blocked():
                # 呼ばずに「探していない」と返す（CostGate も日次カウンタも消費しない）。
                result = pd_radar.blocked_radar_result(
                    session,
                    str(access.document_id or ""),
                    distance=distance,
                    categories=body.categories,
                    keyphrases=body.keyphrases,
                    start=start,
                )
                if isinstance(result.get("seed"), dict):
                    result["seed"] = _with_can_register(
                        result["seed"], access, current_user
                    )
                return result
            result = pd_radar.run_radar_search(
                session,
                str(access.document_id or ""),
                distance=distance,
                categories=body.categories,
                keyphrases=body.keyphrases,
                start=start,
                max_results=max_results,
                # 着地予測・新しい面（VA層 §8）の材料は**注入で渡す**
                # （core/paper_discovery は core.atlas_vectors に触れない境界）。
                anchor_context_resolver=lambda domain_key: _anchor_context(
                    session, domain_key
                ),
            )
        except LookupError as exc:
            raise HTTPException(
                status_code=404, detail=_DETAIL_DOCUMENT_NOT_FOUND
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=_DETAIL_INVALID_DISTANCE) from exc
        except arxiv_client.ArxivApiError as exc:
            logger.info("radar search failed for user=%s: %s", current_user["id"], exc)
            raise HTTPException(status_code=502, detail=_arxiv_unavailable_detail(exc)) from exc
        _persist_arxiv_metadata_cache(session)
    finally:
        session.close()
    # seed を返す全ルートで can_register の注入を揃える（検索後の再描画で
    # 閲覧専用の教員に登録導線が出ないように — 強制は POST 側のゲート）。
    if isinstance(result, dict) and isinstance(result.get("seed"), dict):
        result["seed"] = _with_can_register(result["seed"], access, current_user)
    # 地図と突き合わせられたかどうかは必ず返す（キーの不在で黙らせない — VA8）。
    if isinstance(result, dict):
        result.setdefault("relation_context", {"available": False})
        # 制限されているかどうかも同様。**seed 側と同じ値**に揃える（フロントは
        # seed.arxiv_blocked と top-level の両方を読むので、食い違うと表示が揺れる）。
        seed = result.get("seed")
        result.setdefault(
            "arxiv_blocked",
            bool(seed.get("arxiv_blocked")) if isinstance(seed, dict) else False,
        )
    return result


@router.post("/radar/compare")
def radar_compare(
    body: RadarCompareRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """起点論文と選択候補のアブストラクトを1コールで比較する（PR4）。

    結果は保存しない（レスポンス限りの注釈）。各項目には**サーバ側固定文**の
    ``caveat`` が付く（LLM 出力に依存しない）。素材なしは 422・日次上限は 429・
    候補メタデータ全滅と LLM 失敗は 502（いずれも数値を含まない事実文）。

    arXiv からアクセスを制限されている間は、**要旨が保存済みの写しで揃っていない
    ときだけ** 200 + ``arxiv_blocked=true`` + 空 ``items`` を返す（LLM の日次上限を
    消費しない — 呼べない理由がこちら側の都合ではないため。設計書 §14.7）。写しだけで
    比較できるなら arXiv を呼ばずに通常どおり実行する（§14.3 の予算 0 コール）。
    """
    arxiv_ids = [str(value or "").strip() for value in (body.arxiv_ids or [])]
    arxiv_ids = [value for value in arxiv_ids if value]
    if not arxiv_ids:
        raise HTTPException(status_code=422, detail=_DETAIL_COMPARE_EMPTY)
    if len(arxiv_ids) > pd_compare.RADAR_COMPARE_MAX_CANDIDATES:
        raise HTTPException(status_code=422, detail=_DETAIL_COMPARE_TOO_MANY)

    access = _radar_document_or_404(body.document_ref, current_user)

    session = _pg_session()
    try:
        try:
            if _arxiv_blocked() and pd_radar.compare_requires_arxiv(
                session, str(access.document_id or ""), arxiv_ids
            ):
                # 写しに無い要旨を取りに行けない。比較文を作れないので LLM も呼ばず、
                # **日次上限も消費しない**（教員の持ち分を上流の制限で削らない）。
                return {
                    "items": [],
                    "skipped": [
                        {"arxiv_id": value, "detail": pd_radar.NOTE_ARXIV_BLOCKED}
                        for value in arxiv_ids
                    ],
                    "notes": [pd_radar.NOTE_ARXIV_BLOCKED],
                    "arxiv_blocked": True,
                }
            _consume_radar_compare_quota(current_user["id"])
            result = pd_compare.run_compare(
                session,
                str(access.document_id or ""),
                arxiv_ids,
                user_id=current_user["id"],
            )
            _persist_arxiv_metadata_cache(session)
            if isinstance(result, dict):
                # 制限されていないことも明示する（キーの不在で黙らせない — §14.7）。
                result["arxiv_blocked"] = _arxiv_blocked()
            return result
        except LookupError as exc:
            raise HTTPException(
                status_code=404, detail=_DETAIL_DOCUMENT_NOT_FOUND
            ) from exc
        except pd_compare.NoSeedMaterialError as exc:
            # 素材なしで比較文を創作しない（core の事実文をそのまま渡す）。
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except pd_compare.CompareUnavailableError as exc:
            logger.info("radar compare unavailable for user=%s: %s", current_user["id"], exc)
            raise HTTPException(
                status_code=502, detail=_DETAIL_COMPARE_UNAVAILABLE
            ) from exc
        except arxiv_client.ArxivApiError as exc:
            logger.info("radar compare arXiv failed for user=%s: %s", current_user["id"], exc)
            raise HTTPException(status_code=502, detail=_arxiv_unavailable_detail(exc)) from exc
    finally:
        session.close()


@router.post("/radar/provenance")
def register_radar_provenance(
    body: RadarProvenanceRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """手動アップロードされた教材に arXiv の出所を後から記帳する（3段階の 2・3）。

    レーダーは ``documents.source_url`` が空の教材について、ファイル名・タイトルから
    arXiv ID を**推定**して検索条件を埋める（1段階目・DB 非変更）。ここはその推定を
    **出所として確定**する唯一の書き込み口で、次のいずれかでのみ記帳する:

    - **2段階目（自動）**: arXiv 側のタイトルと教材のタイトルが正規化一致
      （``method="auto_title_match"``）。
    - **3段階目（明示確定）**: 一致しないので教員が ``confirm=true`` を送った
      （``method="teacher_confirmed"``）。

    クライアントが提示した ``arxiv_id`` は信用せず、**サーバが seed を導出し直して**
    突き合わせる（不一致は 422）。照合材料が無い（arXiv に到達できなかった）ときは
    ``confirm=true`` でも記帳しない — 根拠のない出所を書かない。

    権限は view（不在・不可視は同一 404 — PR8）に加えて **edit**（403）。
    記帳は ``theory_review_events``（``AUDIT_ENTITY_PAPER_DISCOVERY``）に監査する。
    """
    access = _radar_document_or_404(body.document_ref, current_user)
    if not _radar_can_register(access, current_user):
        raise HTTPException(status_code=403, detail=_DETAIL_PROVENANCE_FORBIDDEN)

    requested_id = pd_schema.normalize_arxiv_id(body.arxiv_id)
    if not requested_id:
        raise HTTPException(status_code=422, detail=_DETAIL_INVALID_ARXIV_ID)

    document_id = str(access.document_id or "")
    session = _pg_session()
    try:
        try:
            # arXiv 到達の失敗は core が fail-soft で握るため、ここでは
            # ``fetched=False`` として現れる（下の 422 で「照合材料なし」になる）。
            seed = pd_radar.resolve_seed(session, document_id, fetch_arxiv=True)
        except LookupError as exc:
            raise HTTPException(
                status_code=404, detail=_DETAIL_DOCUMENT_NOT_FOUND
            ) from exc

        provenance = dict(seed.get("provenance") or {})
        status = str(provenance.get("status") or "")
        if status == pd_radar.PROVENANCE_STATUS_REGISTERED:
            raise HTTPException(status_code=409, detail=_DETAIL_PROVENANCE_ALREADY)
        if status != pd_radar.PROVENANCE_STATUS_INFERRED or (
            str(provenance.get("arxiv_id") or "") != requested_id
        ):
            raise HTTPException(status_code=422, detail=_DETAIL_PROVENANCE_MISMATCH)
        if not provenance.get("fetched"):
            # 照合材料ゼロで確定させない（confirm でも不可）。
            raise HTTPException(status_code=422, detail=_DETAIL_PROVENANCE_UNVERIFIED)

        title_match = bool(provenance.get("title_match"))
        if not title_match and not body.confirm:
            raise HTTPException(
                status_code=409, detail=_DETAIL_PROVENANCE_TITLE_MISMATCH
            )
        method = _PROVENANCE_METHOD_AUTO if title_match else _PROVENANCE_METHOD_CONFIRMED

        try:
            pd_radar.register_arxiv_provenance(session, document_id, requested_id)
        except ValueError as exc:
            session.rollback()
            # 同時実行で先に記帳された場合など（事実文は内部情報を含めない）。
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.commit()

        # 記帳後の状態（``provenance.status="registered"``・``categories_source="arxiv"``）
        # をそのまま返し、フロントが再取得しなくても表示を切り替えられるようにする。
        # **arXiv には行かない**（この操作で引いたのは上の1回だけ — 設計書 §14）。
        # 要旨・カテゴリは1回目の結果から移す。なお上の commit は、1回目の導出が
        # 残した arXiv メタデータの写し（migration 085）も併せて確定させている。
        registered_seed = _merge_fetched_metadata(
            pd_radar.resolve_seed(session, document_id, fetch_arxiv=False), seed
        )
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    record_review_event(
        AUDIT_ENTITY_PAPER_DISCOVERY,
        document_id or _ENTITY_ID_FALLBACK,
        _STATUS_NONE,
        _STATUS_PROVENANCE_REGISTERED,
        current_user["id"],
        {
            "action": "register_provenance",
            "arxiv_id": requested_id,
            "method": method,
        },
    )
    logger.info(
        "arXiv provenance registered by user=%s document=%s method=%s",
        current_user["id"], document_id, method,
    )
    return {
        "registered": True,
        "seed": _with_can_register(registered_seed, access, current_user),
    }


# ---------------------------------------------------------------------------
# コーパスを補う論文（正本 docs/features/corpus_complement_design.md §5.5）
#
# 「近さ」ではなく「このコーパスに何が足されるか」で候補を選ぶ第3の探し方。
# 本ルータが構造として守るもの:
#
# - **CC2 決定論・非LLM**: このモジュールは LLM を呼ばない。レンズ A/B の埋め込みは
#   既存の関連度バッチ（``ranking.rank_candidates`` の1コール）に相乗りし、発見層の
#   ``core.llm`` 接触点を増やさない。レンズC（参照リスト）は外部 API のみ。
# - **CC3 保存は外部事実のキャッシュだけ**: 候補・レンズ判定は保存しない（PD5）。
#   ``/complement/foundation`` の書き込みは参照リストキャッシュの upsert のみで、
#   教員の判断を含まないため**監査記帳もしない**（``/search`` / ``/citation-search``
#   と同じ扱い）。
# - **CC4 数値非表示**: core の DTO をそのまま返し、cosine・引用元の本数・配置件数を
#   足さない（生値は core の内側から出てこない）。
# - **CC8 fail-soft**: レンズが成立しなくても検索そのものは 200 で成立させ、
#   縮退した**そのレンズだけ** ``available:false`` + 事実文で正直に返す。
# ---------------------------------------------------------------------------


def _complement_block(
    facts: Optional[dict],
    anchor_context: Optional[dict],
) -> dict:
    """レンズ2つの成否を DTO 形へ整える（``{available, skeleton_version?, lenses}``）。

    ``facts`` は ``ranking.rank_candidates`` が返す ``complement_facts``
    （= ``complement.build_complement_context`` の ``facts``）。並べ替えが不成立
    だったときは両レンズが縮退した事実文で渡ってくる（CC8）。

    top-level の ``available`` は**どちらか一方でも成立していれば真**にする
    （片方が縮退しても補完の提示自体は成立するため）。骨格版はアンカーが読めた
    ときだけ添える（VA8 — 版を明示しない言明を作らない）。
    """
    source = dict(facts or pd_complement.degraded_facts())
    lenses: dict[str, Any] = {}
    for name in ("coverage", "skies"):
        lens = dict(source.get(name) or {})
        entry: dict[str, Any] = {"available": bool(lens.get("available"))}
        note = lens.get("note")
        if note:
            entry["note"] = note
        lenses[name] = entry

    block: dict[str, Any] = {
        "available": any(entry["available"] for entry in lenses.values()),
        "lenses": lenses,
    }
    version = str((anchor_context or {}).get("skeleton_version") or "").strip()
    if version:
        block["skeleton_version"] = version
    return block


def _apply_complement_order(session, domain_key: str, result: dict) -> dict:
    """検索結果に補完の注釈を足し、補完のある候補を先頭へ寄せる。

    ``_apply_relevance_order`` と同じ流儀で、**どの段で失敗しても候補を捨てない**。
    材料（薄いノード・前提文）が組めない、並べ替えが不成立、といった縮退はいずれも
    事実文で返し、検索そのものは成立させる（CC8 / PD6）。
    """
    candidates = list(result.get("candidates") or [])
    settings = get_settings()

    anchor_context: Optional[dict] = None
    complement_context: Optional[dict] = None
    try:
        anchor_context = _anchor_context(session, domain_key)
        complement_context = pd_complement.build_complement_context(
            session,
            domain_key,
            anchor_context,
            thin_max_documents=settings.discovery_complement_thin_max_documents,
        )
    except Exception:  # noqa: BLE001 — 補完の材料が組めなくても検索は成立させる
        logger.warning(
            "complement context unavailable for domain %s", domain_key, exc_info=True
        )
        complement_context = None

    try:
        ranked = pd_ranking.rank_candidates(
            session,
            domain_key,
            candidates,
            anchor_context=anchor_context,
            complement_context=complement_context,
        )
    except Exception:  # noqa: BLE001 — 並べ替えの失敗で検索結果を捨てない
        logger.warning("complement ordering failed for domain %s", domain_key, exc_info=True)
        ranked = {
            "available": False,
            "note": pd_ranking.NOTE_UNAVAILABLE,
            "ordered": candidates,
            "complement_facts": pd_complement.degraded_facts(),
        }

    ordered = list(ranked.get("ordered") or candidates)
    try:
        ordered = pd_complement.order_complement_first(ordered)
    except Exception:  # noqa: BLE001 — 並べ替えられなくても候補は返す
        logger.warning(
            "complement ordering (first) failed for domain %s", domain_key, exc_info=True
        )

    payload = dict(result)
    payload["order"] = ORDER_RELEVANCE
    payload["candidates"] = ordered
    ranking_info: dict[str, Any] = {"available": bool(ranked.get("available"))}
    note = ranked.get("note")
    if note:
        ranking_info["note"] = note
    payload["ranking"] = ranking_info
    payload["complement"] = _complement_block(
        ranked.get("complement_facts"), anchor_context
    )
    return payload


@router.post("/complement/search")
def complement_search_candidates(
    body: SearchRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コーパスを補う候補を検索する（レンズA 地図の薄い領域 / レンズB 検証記録の無い前提）。

    候補集合と副作用は ``POST /search`` と**同一**（arXiv 1リクエスト・
    ``last_checked_at`` の更新のみ）。違いは並び順と注釈だけで、

    - 並び順は常に関連度（``body.order`` は**無視する** — 補完の提示は関連度順を
      土台にした安定ソートでのみ意味を持つため）。
    - 補完の根拠がある候補（``candidate.complement``）を先頭へ寄せる。候補は
      捨てない・件数は変えない（PD6）。
    - top-level に ``complement: {available, skeleton_version?, lenses:{coverage, skies}}``
      を足す。レンズは独立に縮退し、片方が成立しなくても他方は動く（CC8）。

    LLM は呼ばない（CC2）。埋め込みは関連度の1バッチに相乗りし、日次ゲート
    （``DISCOVERY_RANKING_MAX_CALLS_PER_DAY``）の消費も1回のまま。arXiv API の失敗は
    **502 + 事実文**で、空一覧を「該当なし」と偽らない（PD6）。
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
            logger.info(
                "arXiv complement search failed for user=%s: %s", current_user["id"], exc
            )
            raise HTTPException(status_code=502, detail=_arxiv_unavailable_detail(exc)) from exc
        session.commit()
        result = _apply_complement_order(session, body.domain_key, result)
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return result


@router.post("/complement/foundation")
def complement_foundation_candidates(
    body: CitationSearchRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """取り込み済み論文の参照リストから「基盤論文」の候補を導出する（レンズC）。

    **候補提示のみ**で取り込みはしない（CC7 — 取り込みは既存の ``/ingest`` /
    ``/ingest-batch`` を教員が明示的に叩く）。候補は保存せず（PD5）、書き込みは
    参照リストという**外部事実**のキャッシュ（migration 077）の upsert だけなので
    監査も記帳しない（CC3。``/search`` / ``/citation-search`` と同じ扱い）。
    その upsert を確定させるため、成功時は必ず ``commit`` する。

    オプトイン（``DISCOVERY_CITATION_SOURCE_ENABLED``）が無効なときは 403 / 404 に
    せず ``{"enabled": false, "note": ...}`` を返す（機能の存在は隠さない）。
    1操作で新たに参照リストを引くシードは ``DISCOVERY_FOUNDATION_FETCH_PER_CALL``
    本までで、残りは ``pending_seeds`` で正直に示す（PD7 の行儀）。参照リストを
    1件も読めなかったときは **502 + 事実文**で、空一覧を「該当なし」と偽らない（PD6）。
    """
    settings = get_settings()
    session = _pg_session()
    try:
        try:
            result = pd_foundation.run_foundation_search(
                session,
                body.domain_key,
                min_citing_seeds=settings.discovery_foundation_min_citing_seeds,
                fetch_per_call=settings.discovery_foundation_fetch_per_call,
                ttl_days=settings.discovery_reference_cache_ttl_days,
            )
        except pd_citation_client.CitationApiError as exc:
            session.rollback()
            logger.info(
                "foundation search failed for user=%s: %s", current_user["id"], exc
            )
            raise HTTPException(
                status_code=502, detail=_DETAIL_CITATION_UNAVAILABLE
            ) from exc
        # 参照リストキャッシュ（外部事実の写し）の upsert を確定させる。
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return result
