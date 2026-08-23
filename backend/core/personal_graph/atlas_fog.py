"""わたしの地図「名前のある霧」（atlas 近傍提示）の導出。

「広がりの装置」（好奇心の情報設計）の装置1。設計の正本は
``docs/features/personal_map_nearby_design.md``（PN-1〜PN-7 / PMN-1〜PMN-7 を継承する）。
不変条項の要点は他の装置と同じ: **存在だけを事実として見せ、詳細は本人の明示操作まで
伏せる**（``journey.py`` の ``cross_course_hint`` と同じ文法）。

本人の痕跡ノードが分野の地図（atlas 骨格）の1概念に結びついているとき、その概念が
属する「領域」と、骨格エッジで直接つながる概念・同じ領域にある他の概念だけを
「近くにある」という事実で示す。座標・件数・confidence・seed_status 等の数値は
一切返さない。

規約:
- FastAPI / routes / services / core.llm を import しない（core/ 規約）。LLM を呼ばない。
- DB 読みは ``core.personal_graph.queries`` のプリミティブ経由のみ
  （``fetch_course_cartridge_id`` / ``fetch_atlas_concept_context``）。
  骨格は凍結版のみを読む ``atlas_store.load_learner_skeleton`` を queries.py 側が使う
  （draft を読む経路は作らない）。書き込みは一切しない。
- 本人以外の user_id を受け取るパラメータを作らない（PN-1）。
"""

from __future__ import annotations

from core.personal_graph import queries
from core.personal_graph.derive import derive_person_network

#: 返す近傍概念の全体上限（骨格エッジ側 + 同領域側を合わせて。件数には言及せず黙って切る）。
MAX_NEIGHBORS = 8

#: 中心が地図に結びついていない・骨格が無い・概念突合不能のいずれでも同じ事実文
#: （区別を学習者に見せない — 欠落を異常として演出しない）。
_NOTE_UNRESOLVED = "この記録は、まだ分野の地図に結びついていません。"

_RELATION_EDGE = "edge"
_RELATION_SIBLING = "sibling"


def _unavailable(note: str = _NOTE_UNRESOLVED) -> dict:
    return {"available": False, "here": None, "neighbors": [], "note": note}


def atlas_neighbors_for_person_node(user_id: str, node_id: str) -> dict | None:
    """本人ノードが結びつく分野の地図上の位置と、その近傍概念を返す。

    読み取り専用・非LLM・DB 非変更（PN-2）。``node_id`` が本人の個人ネットワークに
    無ければ ``None``（route が 404 にする。他人の痕跡を中心にできない・PN-1）。

    中心が地図に結びついていない（``anchor.atlas_node_id`` が無い / ``course_id`` が
    無い）・コースが明示カートリッジを持たない・骨格が無い・概念が骨格中で突合できない、
    のいずれの場合もエラーにせず ``available=False`` + ``note`` を 200 で返す
    （欠落を異常として演出しない・P4）。

    成功時のレスポンス形状::

        {
            "available": True,
            "here": {"label": str, "region_label": str},
            "neighbors": [
                {"id": str, "label": str, "region_label": str, "relation": "edge"},
                {"id": str, "label": str, "region_label": str, "relation": "sibling"},
                ...
            ],
            "note": None,
        }

    ``neighbors`` は骨格エッジで直接つながる概念（``relation="edge"``）を先頭に、
    同じ領域内の他概念（``relation="sibling"``）を続けて並べる。各群の内部順序は
    骨格の出現順（決定論。``queries.fetch_atlas_concept_context`` が既に整えている）。
    重複除去（edge を優先）・自分自身は含めない・全体で最大 ``MAX_NEIGHBORS`` 件
    （黙って切る。件数には言及しない）。
    """
    network = derive_person_network(user_id)
    node = next((n for n in network.nodes if n.id == node_id), None)
    if node is None:
        return None

    atlas_node_id = str(node.anchor.atlas_node_id or "").strip()
    course_id = str(node.course_id or "").strip()
    if not atlas_node_id or not course_id:
        return _unavailable()

    cartridge_id = queries.fetch_course_cartridge_id(course_id)
    if not cartridge_id:
        return _unavailable()

    context = queries.fetch_atlas_concept_context(cartridge_id, atlas_node_id)
    if not context:
        return _unavailable()

    region_label = str(context.get("region_label") or "")
    here = {
        "label": str(context.get("concept_label") or ""),
        "region_label": region_label,
    }

    neighbors: list[dict] = []
    seen_ids: set[str] = set()

    for entry in context.get("edge_neighbors") or []:
        neighbor_id = str(entry.get("id") or "")
        if not neighbor_id or neighbor_id == atlas_node_id or neighbor_id in seen_ids:
            continue
        seen_ids.add(neighbor_id)
        neighbors.append(
            {
                "id": neighbor_id,
                "label": str(entry.get("label") or ""),
                "region_label": str(entry.get("region_label") or ""),
                "relation": _RELATION_EDGE,
            }
        )

    for entry in context.get("sibling_concepts") or []:
        neighbor_id = str(entry.get("id") or "")
        if not neighbor_id or neighbor_id == atlas_node_id or neighbor_id in seen_ids:
            continue
        seen_ids.add(neighbor_id)
        neighbors.append(
            {
                "id": neighbor_id,
                "label": str(entry.get("label") or ""),
                "region_label": region_label,
                "relation": _RELATION_SIBLING,
            }
        )

    return {
        "available": True,
        "here": here,
        "neighbors": neighbors[:MAX_NEIGHBORS],
        "note": None,
    }
