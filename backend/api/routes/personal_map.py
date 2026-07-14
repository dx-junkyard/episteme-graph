"""個人知識ネットワーク（Phase P）学習者向け API（実パス ``/api/learning/...``）。

設計書 ``docs/features/personal_knowledge_network_design.md``（特に §0 不変条項 PN-1〜7 /
§2 ノード導出規則 / §7 API）の Phase P-0 実装。本ファイルは
``core/personal_graph/derive.py::derive_personal_network`` の導出結果をそのまま返すだけの
薄い読み取り専用レイヤー。

不変条項（詳細は設計書 §0）:
- **PN-1 本人のみ可視**: ``current_user["id"]`` 以外の user_id を受け取るパラメータを
  作らない。教員・管理者向けの閲覧・集約 API はここに作らない（Phase B で別途設計・
  k-匿名集約のみ）。
- **PN-2 導出であって記録ではない**: 確定保存・完了フラグを持たない。毎回サーバ状態から
  決定論的に導出する（キャッシュしない）。書き込み系エンドポイントは無い。
- **PN-3 candidate を数えない**: 本人が引き受けた痕跡のみノード化する（tension の
  candidate・anchor の llm_candidate 帰属は除外。導出側 ``core/personal_graph/derive.py``
  の責務）。
- **PN-4 数値を見せない**: レスポンスに集計数値（件数・網羅率・スコア・順位）を足さない。
  ノードの列挙自体は本人のデータなので可。
- **PN-5 非LLM・決定論**: 本ルートは LLM を一切呼ばない。

受講ゲートは ``services.get_accessible_course_data``（``routes/reconstruction.py`` の
学習者向けルートと同じ判定）。コースが見えなければ 404。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from dependencies import _get_current_user
from services import get_accessible_course_data
from core.personal_graph.derive import derive_personal_network

router = APIRouter(prefix="/api/learning", tags=["Learning"])


@router.get("/courses/{course_id}/personal-network")
def get_personal_network(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人の個人知識ネットワークを導出して返す（非LLM・DB 非変更・PN-2）。

    ``derive_personal_network`` が ``interest_traces`` / ``learner_reconstructions`` を
    (user_id, course_id) スコープで読み、本人確定済みの痕跡のみをノード化する（設計書 §2）。
    candidate / dismissed / superseded / llm_candidate 帰属のノードは含まれない（PN-3）。
    集計数値（件数・網羅率等）は含めない（PN-4）。
    """
    course_data = get_accessible_course_data(current_user["id"], course_id)
    if course_data is None:
        raise HTTPException(status_code=404, detail="Course not found")

    network = derive_personal_network(current_user["id"], course_id)
    return network.to_dict()
