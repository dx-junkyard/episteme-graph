"""わたしの記録（主権台帳v1）学習者向け API — 読み取り専用・本人のみ。

正本設計書: ``docs/features/trace_registry_sovereignty_ledger_design.md`` §3.3。
実体は ``core/trace_ledger.py``（fetch_ledger_rows / build_ledger_overview /
build_ledger_export）で、本ルーターは認証ユーザーの user_id を固定して渡す薄い層。

不変条項（routes/personal_map.py の me_router と同型）:

- **TR4 読み取り専用・本人のみ**: GET のみ。書き込み・削除・封印メソッドを
  本ルーターに一切作らない（封印は P4 例外の専用設計書を経る v2。map-exclude 等の
  既存訂正操作は routes/learning.py 側のまま）。ユーザー ID のパスパラメータは
  作らない — 対象は常に ``current_user["id"]``（PN-1 と同型。本人以外の痕跡への
  アクセス経路を構造的に持たない）。
- **受講ゲート不要**: 本人自身の痕跡を横断して読むだけであり、course_id は所有境界
  ではなく出所（provenance）に過ぎない（personal_map.me_router と同じ理由）。
- **TR6 数値を見せない**: 件数バッジ・進捗率・スコアのフィールドを返さない
  （truncated の bool だけは正直に返す）。
- **TR7 ステアリング禁止**: このレスポンスを提示内容・提示順・対話方針の入力にしない。
"""

from __future__ import annotations

import datetime
import json

from fastapi import APIRouter, Depends, Response

from dependencies import _get_current_user
from core.trace_ledger import (
    build_ledger_export,
    build_ledger_overview,
    fetch_course_labels,
    fetch_ledger_rows,
)

me_router = APIRouter(prefix="/api/me", tags=["Learning"])

# 持ち出し（export）は「常に全件」（設計書 §3.2）。一覧の既定上限（500）とは別に、
# 実用上到達しない大きな上限で全行を読む（LIMIT なし SQL を増やさないための定数）。
_EXPORT_ROW_LIMIT = 100_000


@me_router.get("/records")
def get_my_records(
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人の全痕跡の台帳 overview（系統グルーピング + 公表状態の事実文）。

    kind 条件・status 条件を付けない一望（dismissed / superseded / candidate も
    含む — P4）。新しい順・上限 500 行で、超過は ``truncated: true`` で正直に返す。
    """
    rows, truncated = fetch_ledger_rows(current_user["id"])
    course_labels = fetch_course_labels(rows)
    return build_ledger_overview(rows, course_labels=course_labels, truncated=truncated)


@me_router.get("/records/export")
def export_my_records(
    current_user: dict = Depends(_get_current_user),
) -> Response:
    """本人の全痕跡の持ち出し（JSON ダウンロード、payload 全文・無加工）。

    ダウンロード形は routes/discuss_observation.py の observation-dump を踏襲する。
    ただし**学習者本人の持ち出しは監査記帳しない**（意図的な非対称。教員による観測
    ダンプ取得と違い、本人が自分の痕跡を手元に置く行動まで theory_review_events に
    記帳すると観察面の拡大になる — 監査記帳ヘルパーをここから呼ばない。ガードレールが
    不使用を固定する）。
    """
    rows, _truncated = fetch_ledger_rows(current_user["id"], limit=_EXPORT_ROW_LIMIT)
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = build_ledger_export(rows, exported_at=now.isoformat())
    filename = f"my-records-{now.strftime('%Y%m%d')}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
