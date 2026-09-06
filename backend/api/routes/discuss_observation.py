"""discuss 観測基盤（Observation Layer for Phase 3 Gate）API — migration 060。

正本: ``docs/features/discuss_observation_design.md``。discuss モード（「論文と話す」）の
Phase 3（v2）着手判断を「実測ゲート」で行えるようにするための3点セット
（①蓄積状況の可視化 ②観測ダンプ ③UI イベント取込）を提供する。

- ``admin_router``（main.py が ``/api/admin`` で登録。SYSTEM_ADMIN のみ）:
    - ``GET /discuss/observation-status`` — 蓄積状況 + 分析開始の参考目安
      （``core.discuss.observation.build_observation_status``。DO5: 自動ゲートにしない）
    - ``GET /discuss/observation-dump`` — 分析用ダンプ（tar.gz/zip、本文非含有・仮名化。
      取得を ``theory_review_events``（``entity_type='discuss_observation'``）に監査）
- ``learning_router``（main.py が prefix なしでそのまま登録。認証必須・本人記録のみ）:
    - ``POST /discuss/metric-events`` — discuss UI イベントの取込
      （フロントは fire-and-forget 前提。DO6）

学習者に数値を見せる API はここでは作らない（DO3）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from dependencies import _get_current_user, _require_system_admin
from core.discuss import observation
from core.schema import AUDIT_ENTITY_DISCUSS_OBSERVATION
from services import record_review_event

logger = logging.getLogger(__name__)

# main.py が `/api/admin` prefix でフラット登録する（Tier 3-17c、reconstruction.py と同型）。
admin_router = APIRouter(tags=["Discuss Observation"])
# main.py が prefix なしでそのまま登録する（自身が /api/learning を持つ）。
learning_router = APIRouter(prefix="/api/learning", tags=["Learning"])


_MAX_METRIC_EVENTS_PER_REQUEST = 20


class DiscussMetricEventItem(BaseModel):
    event: str
    course_id: str = ""
    payload: dict = Field(default_factory=dict)


class DiscussMetricEventsRequest(BaseModel):
    events: list[DiscussMetricEventItem]


# ---------------------------------------------------------------------------
# 学習者向け: discuss UI イベント取込（本人記録のみ）
# ---------------------------------------------------------------------------


@learning_router.post("/discuss/metric-events", status_code=201)
def record_discuss_metric_events(
    body: DiscussMetricEventsRequest,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """discuss の UI 操作イベント（開幕・着地・分岐チップ等）を取り込む。

    フロントは fire-and-forget（await しない・失敗は無視）で呼ぶ前提（DO6）。
    レスポンスは件数のみで、学習者向けの数値表示には使わない（DO3）。
    イベント語彙・payload キーはサーバ側でホワイトリスト検証する（DO1）。
    """
    events = body.events
    if len(events) > _MAX_METRIC_EVENTS_PER_REQUEST:
        raise HTTPException(
            status_code=422,
            detail=(
                f"1リクエストにつき最大{_MAX_METRIC_EVENTS_PER_REQUEST}件までです"
                f"（受信: {len(events)}件）"
            ),
        )
    unknown = sorted({e.event for e in events if e.event not in observation.METRIC_EVENT_VOCAB})
    if unknown:
        raise HTTPException(status_code=422, detail=f"未知のイベント種別です: {unknown}")

    user_id = str(current_user.get("id") or "")
    if not user_id:
        raise HTTPException(status_code=403, detail="帰属（ユーザーID）が確認できません")

    payload_events = [
        {"event": e.event, "course_id": e.course_id or "", "payload": e.payload or {}}
        for e in events
    ]
    try:
        recorded = observation.insert_metric_events(user_id, payload_events)
    except Exception:
        logger.exception("discuss metric events insert failed")
        raise HTTPException(status_code=500, detail="イベントの記録に失敗しました")
    return {"recorded": recorded}


# ---------------------------------------------------------------------------
# 教員向け（SYSTEM_ADMIN のみ）: 観測状況・ダンプ
# ---------------------------------------------------------------------------


@admin_router.get("/discuss/observation-status")
def get_discuss_observation_status(
    current_user: dict = Depends(_require_system_admin),
) -> dict:
    """discuss 観測状況（蓄積量 + 分析開始の参考目安, 設計書 §4）。

    参考目安（``criteria`` / ``ready_for_analysis``）は表示専用（DO5）。到達状況を
    返すだけで、これを見て何かが自動的に切り替わることはない — 判断は常にオーナー。
    """
    status = observation.build_observation_status()
    # 制度指標カタログへの参照（IG1）。定義は
    # GET /api/indicators/discuss-observation-status。
    status["indicator_id"] = "discuss-observation-status"
    return status


@admin_router.get("/discuss/observation-dump")
def get_discuss_observation_dump(
    format: str = Query(default="tar.gz"),
    current_user: dict = Depends(_require_system_admin),
) -> Response:
    """discuss 観測ダンプ（tar.gz/zip、本文非含有・仮名化, 設計書 §5）。

    取得を ``theory_review_events``（``entity_type='discuss_observation'``）に監査する。
    """
    if format not in observation.DUMP_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"format には {list(observation.DUMP_FORMATS)} のいずれかを指定してください"
                f"（受信値: {format!r}）"
            ),
        )

    content, filename = observation.build_observation_dump(format)
    media_type = "application/gzip" if format == "tar.gz" else "application/zip"

    # 取得の監査（本文はダンプ自体に含まれない — DO1。ここで記録するのは「誰が・いつ・
    # どの形式を取得したか」のみ）。
    record_review_event(
        AUDIT_ENTITY_DISCUSS_OBSERVATION,
        "discuss_observation_dump",
        "",
        "downloaded",
        current_user.get("id"),
        {"format": format},
    )

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
