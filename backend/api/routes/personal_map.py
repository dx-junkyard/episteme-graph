"""個人知識ネットワーク（Phase P）学習者向け API。

設計書は2本ある: ``docs/features/personal_knowledge_network_design.md``（Phase P-0/1/2、
§0 不変条項 PN-1〜7 / §2 ノード導出規則 / §7 API）と、それを継承・拡張する
``/Users/Shared/issues/episteme_graph_personal_knowledge_network_ux_proposal.md``
（§5・§9 Phase P-0.5「意味論を修正する」）。

**Phase P-0.5 の意味論（本ファイルの2系統の router）**:
個人ネットワークの所有単位は常に ``user_id``（本人）であり、``course_id`` は所有境界
ではなく **provenance（学習が起きた出所）** に過ぎない（提案書 §5.1）。

- ``router``（``/api/learning/courses/{course_id}/personal-network...``、既存 Phase P-0/P-2）:
  「コースビュー」— 互換ラッパーとして維持する。コース単位の受講ゲート
  （``get_accessible_course_data``）を通した上で、そのコースの痕跡だけを見せる。
- ``me_router``（``/api/me/personal-network...``、Phase P-0.5 で追加）: **正本 API**。
  本人の全コースを横断した個人ネットワークを ``core/personal_graph/derive.py::
  derive_person_network`` で導出する。``context_type=course&context_id=...`` を渡すと
  結果をそのコースの痕跡に絞り込めるが、それは「コースビューへの投影」であって
  所有境界ではない（提案書完了条件: API 名がコース所有を最終形として固定していない）。

両 router とも読み取り専用の薄いレイヤーで、確定保存・完了フラグは持たない（PN-2）。
``me_router`` に書き込み・削除系メソッドの登録は一切行わない（読み取り専用の構造的固定）。

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
- **PN-6/fail-closed（提案書 §5.3 の ``include_candidate_links``）**: 候補リンク
  （tension `candidate`・anchor `llm_candidate` 等の本人未確定情報）を個人ネットワークに
  含める経路をこの API に作らない。``include_candidate_links=true`` は 422（サーバ側が
  拒否する。クライアントに opt-in させない＝fail-closed）。

``router`` の受講ゲートは ``services.get_accessible_course_data``（コースが見えなければ
404）。``me_router`` は本人自身の痕跡を横断するだけなので受講ゲートを要求しない
（course_id は出所フィルタに過ぎない・PN-1）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from dependencies import _get_current_user
from services import get_accessible_course_data, user_can_view_document
from core.personal_graph.derive import (
    derive_person_network,
    derive_personal_network,
    group_nodes_by_anchor,
)
from core.personal_graph.journey import journey_for_node
from core.personal_graph.provisional import derive_provisional_nodes
from core.personal_graph.queries import fetch_course_titles
from core.personal_graph.schema import PersonalNetwork

router = APIRouter(prefix="/api/learning", tags=["Learning"])

# Phase P-0.5（提案書 §5.3）: 本人スコープの正本 API。
me_router = APIRouter(prefix="/api/me", tags=["Learning"])

# context_type の許可語彙。提案書 §5.3 は course/document/anchor_id 等を将来像として
# 挙げているが、Phase P-0.5 の実装範囲は course のみ（document 単位のコンテキストは
# 別 issue）。未対応の語彙は 422（fail-closed。黙って無視しない）。
_ALLOWED_CONTEXT_TYPES = ("course",)


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

    ``provisional_nodes``（カテゴリギャップ候補 v1-b。正本は
    ``docs/features/category_gap_candidates_design.md`` §4.4 裁定 / §5.6）: このコースの
    sources 由来 document について、共有骨格に置けなかった主題を**本人にだけ**見せる
    暫定ノード。``core.personal_graph.provisional`` が ``landscape_gap_signals`` の
    ``active`` 行から読み時に導出するだけで、共有骨格・共有候補（``atlas_gap_decisions``）
    には一切書き込まない・読まない。骨格の無いコース・取得失敗は空リスト（fail-closed）で、
    ``confidence`` / ``weight`` / 件数などの数値は載せない（PN-4 / LS5）。
    """
    course_data = get_accessible_course_data(current_user["id"], course_id)
    if course_data is None:
        raise HTTPException(status_code=404, detail="Course not found")

    network = derive_personal_network(current_user["id"], course_id)
    payload = network.to_dict()
    # 読み取り専用の追加フィールド（DB 非変更・PN-2）。導出失敗は [] へ縮退する。
    payload["provisional_nodes"] = derive_provisional_nodes(course_id)
    return payload


@router.get("/courses/{course_id}/personal-network/journey")
def get_personal_network_journey(
    course_id: str,
    node_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人ノードを起点にした旅の経路探索（設計書 §6/§7、Phase P-2）。

    非LLM・決定論的な traversal（``core.personal_graph.journey.journey_for_node``）を
    明示操作（フロントの「ここから旅に出る」ボタン）でのみ呼ぶ薄い読み取り専用エンドポイント。
    DB は非変更（PN-2）。``node_id`` が本人の個人ネットワークに存在しない場合は 404
    （コースが見えない場合と同じく fail-closed）。

    起点アンカーが解決する document の閲覧可否は ``services.user_can_view_document`` で
    判定し、``journey_for_node`` にコールバックとして注入する（``core/personal_graph/`` は
    FastAPI / services を import しない規約のため、権限判定の実体は呼び出し側=本ルートが
    持つ）。閲覧不可なら journey 側が黙って [1]〜[3] 区間を省く（PN-7）。
    """
    course_data = get_accessible_course_data(current_user["id"], course_id)
    if course_data is None:
        raise HTTPException(status_code=404, detail="Course not found")

    result = journey_for_node(
        current_user["id"], course_id, node_id,
        can_view_document=user_can_view_document,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return result


# ---------------------------------------------------------------------------
# Phase P-0.5: /api/me/personal-network（正本 API、提案書 §5.3）
# ---------------------------------------------------------------------------


@me_router.get("/personal-network")
def get_me_personal_network(
    context_type: str | None = None,
    context_id: str | None = None,
    focus_anchor_id: str | None = None,
    include_candidate_links: bool = False,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人スコープの個人知識ネットワーク正本 API（提案書 §5.1〜5.3、Phase P-0.5）。

    ``derive_person_network`` が本人の全コースを横断して痕跡を読み、
    ``core/personal_graph/derive.py::build_person_network`` が課す本人確定ルール
    （設計書 §2・PN-3）に従ってノード化する。受講ゲートは要求しない —
    自分自身の痕跡を見るだけであり、``course_id`` は所有境界ではなく出所フィルタに
    過ぎない（PN-1・提案書 §5.1）。

    クエリパラメータ:
    - ``context_type`` / ``context_id``: 現時点で対応する ``context_type`` は
      ``"course"`` のみ（それ以外は 422・fail-closed）。指定時は結果をその
      ``course_id`` のノードへ絞り込む（＝コースビューへの投影。所有境界の変更ではない）。
      ``context_type`` を指定したのに ``context_id`` が無ければ 422。
    - ``focus_anchor_id``: 指定時は ``anchor.anchor_id`` が一致するノードのみに絞る
      （提案書 §5.3 が挙げる境界付き局所ネットワークの入口。v1 は単純な一致フィルタ）。
    - ``include_candidate_links``: **true を渡すと 422**。候補リンク（tension
      candidate・structure_anchor の llm_candidate 等、本人未確定の情報）は本人が
      確定するまで個人ネットワークに含めない（PN-3/fail-closed。opt-in させない）。

    レスポンス: ``{"nodes": [...], "edges": [...], "anchor_groups": [...],
    "courses": {course_id: {"title": str}}}``。``edges`` は絞り込み後の node 集合に
    ``from_node_id`` が含まれるものだけを返す。``courses`` はノードに現れる
    course_id のうちタイトルが引けたものだけ（コース削除後もタイトルは引けなくなるが
    ノード自体は本人の地図に残る — 提案書 §9 Phase P-0.5 の完了条件）。
    集計数値（件数・網羅率等）は一切含めない（PN-4）。
    """
    if include_candidate_links:
        # 候補リンクは本人確定まで個人ネットワークに含めない（PN-3/PN-6 の fail-closed）。
        # opt-in の余地を作らないため、指定された時点で拒否する（黙って無視しない）。
        raise HTTPException(
            status_code=422,
            detail="候補リンクは本人確定まで個人ネットワークに含めない",
        )
    if context_type is not None and context_type not in _ALLOWED_CONTEXT_TYPES:
        raise HTTPException(
            status_code=422,
            detail="context_type は 'course' のみ対応している",
        )
    if context_type is not None and not context_id:
        raise HTTPException(
            status_code=422,
            detail="context_type を指定する場合は context_id が必須",
        )

    network = derive_person_network(current_user["id"])

    nodes = network.nodes
    if context_type == "course":
        nodes = [n for n in nodes if n.course_id == context_id]
    if focus_anchor_id:
        nodes = [n for n in nodes if n.anchor.anchor_id == focus_anchor_id]

    node_ids = {n.id for n in nodes}
    edges = [e for e in network.edges if e.from_node_id in node_ids]

    filtered_network = PersonalNetwork(nodes=nodes, edges=edges)
    anchor_groups = group_nodes_by_anchor(filtered_network)

    course_ids = sorted({n.course_id for n in nodes if n.course_id})
    titles = fetch_course_titles(course_ids)
    courses = {cid: {"title": titles[cid]} for cid in course_ids if cid in titles}

    return {
        "nodes": [n.to_dict() for n in nodes],
        "edges": [e.to_dict() for e in edges],
        "anchor_groups": anchor_groups,
        "courses": courses,
    }


@me_router.get("/personal-network/journey")
def get_me_personal_network_journey(
    node_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """本人スコープの旅の経路探索（提案書 §5.3、設計書 §6/§7 を継承、Phase P-0.5）。

    ``core.personal_graph.journey.journey_for_person_node``（本人スコープ版。
    ``core/personal_graph/journey.py`` 側で並行実装）を呼ぶだけの薄い読み取り専用
    エンドポイント。コース単位の受講ゲートは要求しない — 本人自身の痕跡を起点にした旅
    であり、course_id はもはや所有境界ではない（PN-1・提案書 §5.1）。閲覧不可な
    document への hop は ``journey_for_person_node`` 側が ``can_view_document``
    コールバックで黙って省く（PN-7）。DB は非変更（PN-2）。

    ``node_id`` が本人の個人ネットワークに存在しない場合は 404。
    """
    from core.personal_graph.journey import journey_for_person_node

    result = journey_for_person_node(
        current_user["id"], node_id,
        can_view_document=user_can_view_document,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return result
