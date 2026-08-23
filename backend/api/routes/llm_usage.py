"""LLM トークン使用量推計（U層）API — migration 043。

実パス: ``/api/admin/llm-usage/...``。設計の正本は
``docs/features/llm_usage_metering_design.md`` §7・§9・§10（library.py と同型の
単独マウント型ルーター、開発ルール2/7）。

- ``GET /metrics``（SYSTEM_ADMIN のみ）: 実測/推計を分離した集計（U1）。
  ``core.llm_usage.metrics.collect_metrics`` を薄くラップするだけ。cost_usd は価格表
  未設定なら null（U7）、dropped_events を必ず返す（U8）。
- ``GET /estimate/documents/{document_id}``（TEACHER 以上）: 解析実行前のトークン量
  事前見積り。図画像 API（``routes/admin.py``）と同じ権限ゲート
  ``_ensure_document_viewable``（``routes/theory_components.py``）を再利用する
  （自前実装しない）。レンジのみ返し、点推定・cost_usd は一切含めない（U5）。
- DELETE / POST / PATCH ハンドラは作らない（``llm_usage_events`` は append-only 台帳。U6）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from dependencies import _require_system_admin, _require_teacher
from routes.theory_components import _ensure_document_viewable

from core.llm_usage.document_estimate import estimate_document_run
from core.llm_usage.forecast import forecast_document_run, forecast_run_capacity
from core.llm_usage.metrics import collect_metrics
from core.postgres import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/llm-usage", tags=["LLM Usage"])

# metrics の from/to 省略時のデフォルト参照期間（直近30日）。捏造ではなく
# 「未指定ならこの窓を見る」という運用上の既定値。実際に使った from/to は
# レスポンスにそのまま返す（呼び出し側が推測しなくてよいように）。
_DEFAULT_LOOKBACK_DAYS = 30


def _default_date_range() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=_DEFAULT_LOOKBACK_DAYS)
    return start.isoformat(), now.isoformat()


def _parse_group_by(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _canonical_document_id(chunks: list[dict], fallback: str) -> str:
    """document_figures.document_id は canonical な documents.id（UUID文字列）で保存されて
    いるため、chunks から解決した canonical な document_id を優先する
    （``routes/admin.py`` の図画像 API と同じ考え方。ただし当該ファイルは並行編集中の
    ため、ここではロジックのみを最小限に複製し import はしない）。
    """
    for chunk in chunks:
        did = chunk.get("document_id")
        if did:
            return did
    return fallback


@router.get("/metrics")
def get_llm_usage_metrics(
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    group_by: str = Query(default="feature"),
    current_user: dict = Depends(_require_system_admin),
) -> dict:
    """使用量集計（設計書 §7-1）。usage_source（reported/estimated）別に分離して返す。"""
    default_from, default_to = _default_date_range()
    effective_from = date_from or default_from
    effective_to = date_to or default_to
    fields = _parse_group_by(group_by)

    session = get_session()
    try:
        return collect_metrics(
            session, date_from=effective_from, date_to=effective_to, group_by=fields
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        session.close()


@router.get("/estimate/documents/{document_id}")
def get_document_usage_estimate(
    document_id: str,
    analyze_images: bool = Query(default=False),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """事前トークン見積り（設計書 §7-2）。TEACHER 以上・レンジのみ・cost_usd 無し（U5）。"""
    chunks = _ensure_document_viewable(document_id, current_user)
    canonical_document_id = _canonical_document_id(chunks, document_id)

    session = get_session()
    try:
        result = estimate_document_run(
            session, canonical_document_id, analyze_images=analyze_images
        )
    finally:
        session.close()

    if result is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return result


@router.get("/forecast")
def get_run_capacity_forecast(
    analyze_images: bool = Query(default=False),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コスト見通しの一行（document 不要版・アップロードゾーン用, 教員支援 Phase 4 §3.1）。

    末端4ステージの日次カウンタ残数のみによる保守判定。返却は
    ``{show: bool, message: str}`` のみ — 数値・残回数・トークン数は返さない（TT2）。
    導出失敗は ``{show: false}`` の fail-open（TT4: 開始をブロックしない）。
    """
    return forecast_run_capacity(analyze_images=analyze_images)


@router.get("/forecast/documents/{document_id}")
def get_document_run_forecast(
    document_id: str,
    analyze_images: bool = Query(default=False),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コスト見通しの一行（document 版・再解析モーダル用, 教員支援 Phase 4 §3.1）。

    ``estimate_document_run`` の上振れ × カウンタ残数の合成による保守判定
    （``core/llm_usage/forecast.py``）。権限ゲートは estimate と同じ
    ``_ensure_document_viewable``（fail-closed のまま — fail-open は導出のみ）。
    返却は ``{show: bool, message: str}`` のみ（TT2 / TT4）。
    """
    chunks = _ensure_document_viewable(document_id, current_user)
    canonical_document_id = _canonical_document_id(chunks, document_id)

    session = get_session()
    try:
        return forecast_document_run(
            session, canonical_document_id, analyze_images=analyze_images
        )
    finally:
        session.close()
