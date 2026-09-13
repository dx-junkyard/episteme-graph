"""コーパス回遊層 — 学習者向け API（実パス ``/api/learning/corpus/...``）。

正本: ``docs/features/corpus_roaming_design.md``（不変条項 CR1〜CR10。§4 = コーパス地図
Phase A / §6 = 地図の端 Phase C / §7 = 関心信号 Phase D）。

本ルータが構造として守るもの:

- **CR1 document 可視性が唯一のゲート（fail-closed）** — コース受講ゲートを一切
  経由しない代わりに、各エンドポイントで ``services.list_visible_document_ids``
  を取り、``core.corpus_view`` へ渡して SQL 内で交差させる。可視集合が空なら
  SQL を発行せず空の結果になる。
- **CR2 既存のコース学習を壊さない** — 既存 API を呼び替えない。骨格そのもの
  （領域配置・座標）は既存の ``GET /api/atlas?cartridge={domain_key}`` が返すため、
  ここでは配置・端だけを返す（描画資産を二重管理しない）。
- **CR5 好奇心の文法** — 端は「開いたときに見えるだけ」。バッジ・件数・督促を返さない。
- **CR6 学習者を監視しない** — 関心は本人の明示 POST のみ記録し、閲覧・滞在の
  暗黙計測を関心として扱わない。教員へは k-匿名集約のみ（本ルータには出さない）。
- **CR7 学習者起点で外部 API を呼ばない** — arXiv / Semantic Scholar のクライアントを
  import しない（外の輪は教員の最終検索が残したビットからの読み時導出）。
- **CR8 情報を落とさない** — 関心の取り消しは ``status`` 遷移。**DELETE ルートは無い**。
- **CR9 同期パスに LLM を入れない** — 本ルータは LLM 0回・embedding 0回。

監査は記帳しない（学習者本人の回遊・関心タップの記帳は観察面の拡大 — 主権台帳 v1 と
同じ判断。設計書 §8）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from dependencies import _get_current_user
from services import (
    list_visible_document_ids,
    record_frontier_interest,
    withdraw_frontier_interest,
)

from core import corpus_view

logger = logging.getLogger(__name__)

# main.py が直接登録する（cycle.py / reconstruction.py と同型の学習者向け router）。
learning_router = APIRouter(prefix="/api/learning", tags=["Learning"])

_DETAIL_DOMAIN_REQUIRED = "分野を指定してください。"
_DETAIL_NO_SKELETON = "この分野には凍結済みの骨格がありません。"
_DETAIL_INVALID_RING = "端の指定が正しくありません。"
_DETAIL_TRACE_NOT_FOUND = "この記録は見つかりません。"
_DETAIL_RECORD_FAILED = "記録できませんでした。時間をおいて再度お試しください。"


def _session():
    """読み取り用セッション。取得不能は 503（読み出しが本体）。"""
    try:
        from core.postgres import get_session

        return get_session()
    except Exception as exc:  # noqa: BLE001
        logger.error("corpus view DB session unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="データベースに接続できません") from exc


@learning_router.get("/corpus/domains")
def get_corpus_domains(current_user: dict = Depends(_get_current_user)) -> dict:
    """凍結骨格を持つ active ドメインの一覧（§4.1）。

    ``has_visible_papers`` は bool のみで**件数を返さない**（CR3）。
    """
    visible = list_visible_document_ids(current_user["id"])
    session = _session()
    try:
        domains = corpus_view.list_corpus_domains(session, visible)
    finally:
        session.close()
    return {"domains": domains}


@learning_router.get("/corpus/landscape")
def get_corpus_landscape(
    domain_key: str = Query(""),
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """1ドメインのコーパス地図（配置 + 縁 + 外）（§4.1 / §6）。

    骨格が無ければ **404**（地図領域ごと非表示 — atlas の fail-closed の流儀）。
    """
    key = (domain_key or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail=_DETAIL_DOMAIN_REQUIRED)

    visible = list_visible_document_ids(current_user["id"])
    session = _session()
    try:
        result = corpus_view.build_corpus_landscape(session, key, visible)
    finally:
        session.close()
    if result is None:
        raise HTTPException(status_code=404, detail=_DETAIL_NO_SKELETON)
    return result


@learning_router.get("/corpus/documents")
def get_corpus_documents(
    domain_key: str = Query(""),
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """この分野に関係づけられた可視論文の一覧（§4.1）。新しい順・数値スコアなし。"""
    key = (domain_key or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail=_DETAIL_DOMAIN_REQUIRED)

    visible = list_visible_document_ids(current_user["id"])
    session = _session()
    try:
        documents = corpus_view.list_corpus_documents(session, key, visible)
    finally:
        session.close()
    return {"documents": documents}


class FrontierInterestRequest(BaseModel):
    domain_key: str
    ring: str
    region_id: str | None = None


@learning_router.post("/corpus/frontier-interest", status_code=201)
def post_frontier_interest(
    body: FrontierInterestRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """「この先を知りたい」の1タップ記録（Phase D / §7）。

    記録するのは ``domain_key`` / ``region_id`` / ``ring`` だけで、本文・質問文を
    持たない（CR6）。この信号は購読条件・取り込み・骨格の何も自動変更しない（CR10）。
    """
    key = (body.domain_key or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail=_DETAIL_DOMAIN_REQUIRED)
    ring = (body.ring or "").strip()
    if ring not in corpus_view.RINGS:
        raise HTTPException(status_code=422, detail=_DETAIL_INVALID_RING)

    trace_id = record_frontier_interest(
        current_user["id"], key, ring, region_id=(body.region_id or "")
    )
    if trace_id is None:
        raise HTTPException(status_code=500, detail=_DETAIL_RECORD_FAILED)
    return {"ok": True, "trace_id": trace_id}


@learning_router.post("/corpus/frontier-interest/{trace_id}/withdraw")
def post_frontier_interest_withdraw(
    trace_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """関心の取り消し（``status='dismissed'`` 遷移のみ — 行削除しない, CR8）。

    他人の行・存在しない行はどちらも 404（存在を知らせない fail-closed）。
    """
    if not withdraw_frontier_interest(current_user["id"], trace_id):
        raise HTTPException(status_code=404, detail=_DETAIL_TRACE_NOT_FOUND)
    return {"ok": True}
