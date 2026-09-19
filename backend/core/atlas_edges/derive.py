"""分野マップの関係表示（RE層）— 辺候補の**読み時導出**（設計書 §4）。

正本: ``docs/features/atlas_relation_edges_design.md`` §4（不変条項 RE1〜RE8 は §2）。

2つの出所から候補を出す:

1. **vector**（:data:`schema.ORIGIN_VECTOR`）— VA層の**保存済み**アンカープロトタイプ
   同士の cosine が最上位帯（``ANCHOR_NEARNESS_THRESHOLD_NEAR``）以上。
2. **co_occurrence**（:data:`schema.ORIGIN_CO_OCCURRENCE`）— 両ノードに live 配置
   （``status NOT IN ('superseded', 'rejected')``）を持つ distinct document が
   :data:`schema.MIN_DOCUMENTS_FOR_EDGE` 件以上。

不変条項の写像:

- **RE6 候補は読み時導出・embedding を呼ばない**: 入力は「保存済みアンカー」と
  「配置行」だけ。本モジュールは ``core.llm`` にも ``atlas_vectors.builder`` の
  埋め込み関数にも触れない（アンカーは呼び出し側が渡す）。
- **RE4 数値非表示**: cosine の生値・共起件数は DTO に載せない。近さは段階ラベル、
  共起の支持は論文タイトルの列挙。生値は :func:`derive_vector_pairs` の内部キー
  ``_similarity``（並び順の根拠）までで、公開 DTO には現れない。
- **RE7 ヘアボール防止の一部**（concept–concept のみ・同一 region 内ペア除外・
  既存骨格辺の無向除外・骨格に無い node の除外）はここで効かせる。本数の上限は
  糸レイヤー側（:mod:`core.atlas_edges.threads`）の責務。
- **決定論**: 候補は ``(from_id, to_id)`` の昇順、支持論文は ``(title, document_id)``
  の昇順で並べる（同じ入力なら常に同じ出力）。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import text as sa_text

from core.atlas_edges import schema
from core.atlas_vectors.query import cosine_similarity
from core.label_vocab import ANCHOR_NEARNESS_SCALE, ANCHOR_NEARNESS_THRESHOLD_NEAR
from core.landscape import schema as landscape_schema

#: 骨格ノードの種別（``AnchorVector.node_kind`` / 配置の対象）。v1 は concept のみ。
NODE_KIND_CONCEPT = "concept"

#: 共起の母集団から外す配置ステータス（教員が却下したもの・再解析で置換されたもの）。
EXCLUDED_PLACEMENT_STATUSES = (
    landscape_schema.STATUS_SUPERSEDED,
    landscape_schema.STATUS_REJECTED,
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


# ---------------------------------------------------------------------------
# 骨格の投影（除外規則の材料）
# ---------------------------------------------------------------------------


def concept_index(skeleton: Any) -> dict[str, dict]:
    """骨格の concept を ``node_id -> {label, region_id}`` に投影する。

    region は含めない（v1 は concept–concept のみ = RE7）。骨格が読めない場合は
    空 dict（候補ゼロへ fail-closed）。
    """
    out: dict[str, dict] = {}
    for region in getattr(skeleton, "regions", ()) or ():
        region_id = _clean(getattr(region, "id", ""))
        for concept in getattr(region, "concepts", ()) or ():
            concept_id = _clean(getattr(concept, "id", ""))
            if not concept_id:
                continue
            out[concept_id] = {
                "label": _clean(getattr(concept, "label", "")) or concept_id,
                "region_id": region_id,
            }
    return out


def existing_edge_pairs(skeleton: Any) -> set[tuple[str, str]]:
    """骨格に既にある辺の**無向**ペア集合（RE8: 凍結された辺は候補から消える）。"""
    pairs: set[tuple[str, str]] = set()
    for edge in getattr(skeleton, "edges", ()) or ():
        left, right = schema.undirected_pair(
            getattr(edge, "from_id", ""), getattr(edge, "to_id", "")
        )
        if left and right:
            pairs.add((left, right))
    return pairs


def _is_excluded_pair(
    left: str,
    right: str,
    *,
    concepts: Mapping[str, dict],
    edge_pairs: set[tuple[str, str]],
) -> bool:
    """v1 の除外規則（§4）。自己ループ・骨格外 node・同一 region・既存辺。"""
    if not left or not right or left == right:
        return True
    node_left = concepts.get(left)
    node_right = concepts.get(right)
    if node_left is None or node_right is None:
        return True
    if node_left["region_id"] and node_left["region_id"] == node_right["region_id"]:
        # 同じ領域の中は地形が既に「近い」と言っている（RE7 ヘアボール防止）。
        return True
    return (left, right) in edge_pairs


# ---------------------------------------------------------------------------
# 1. vector 由来（保存済みアンカーの読みのみ）
# ---------------------------------------------------------------------------


def derive_vector_pairs(skeleton: Any, anchors: Iterable[Any]) -> list[dict]:
    """アンカープロトタイプ同士が最上位帯の近さにある concept ペア（§4 ①）。

    戻り値の各要素は ``{from_id, from_label, to_id, to_label, nearness_label,
    _similarity}``。並びは **cosine 降順 → (from_id, to_id) 昇順**（同値の入れ替わりを
    起こさない決定論。糸レイヤーの貪欲な上限適用がこの順に依存する）。

    ``_similarity`` は**この層の内部専用**（RE4）。公開 DTO を組む
    :func:`derive_edge_candidates` と :mod:`core.atlas_edges.threads` は必ず落とすこと。
    """
    concepts = concept_index(skeleton)
    edge_pairs = existing_edge_pairs(skeleton)

    usable = [
        anchor
        for anchor in (anchors or [])
        if _clean(getattr(anchor, "node_kind", "")) == NODE_KIND_CONCEPT
        and _clean(getattr(anchor, "node_id", "")) in concepts
        and getattr(anchor, "vector", None)
    ]
    usable.sort(key=lambda a: _clean(getattr(a, "node_id", "")))

    out: list[dict] = []
    for i, left_anchor in enumerate(usable):
        left_id = _clean(left_anchor.node_id)
        for right_anchor in usable[i + 1 :]:
            right_id = _clean(right_anchor.node_id)
            left, right = schema.undirected_pair(left_id, right_id)
            if _is_excluded_pair(
                left, right, concepts=concepts, edge_pairs=edge_pairs
            ):
                continue
            similarity = cosine_similarity(left_anchor.vector, right_anchor.vector)
            if similarity is None or similarity < ANCHOR_NEARNESS_THRESHOLD_NEAR:
                # 未測定（次元不一致・ゼロベクトル）は「近い」と言わない（VA4 慎重側）。
                continue
            out.append(
                {
                    "from_id": left,
                    "from_label": concepts[left]["label"],
                    "to_id": right,
                    "to_label": concepts[right]["label"],
                    "nearness_label": ANCHOR_NEARNESS_SCALE.label_for(similarity),
                    "_similarity": float(similarity),
                }
            )
    out.sort(key=lambda item: (-item["_similarity"], item["from_id"], item["to_id"]))
    return out


# ---------------------------------------------------------------------------
# 2. co_occurrence 由来（配置行の読みのみ）
# ---------------------------------------------------------------------------


def _fetch_placements(session: Any, domain_key: str) -> list:
    """当該ドメインの live 配置（``document_id`` / タイトル / ``node_id``）。

    ``corpus_view._visible_placements`` と同じ形だが、**可視性ゲートを持たない**
    （教員のレビューキュー用。学習者向けの糸は vector 由来のみで、共起は使わない）。
    """
    return session.execute(
        sa_text(
            """
            SELECT p.document_id::text,
                   COALESCE(NULLIF(d.title, ''), NULLIF(d.filename, ''), '') AS title,
                   p.node_id
              FROM landscape_placements p
              JOIN documents d ON d.id = p.document_id
             WHERE p.domain_key = :domain_key
               AND NOT (p.status = ANY(:excluded_statuses))
             ORDER BY p.node_id, title, p.document_id
            """
        ),
        {
            "domain_key": domain_key,
            "excluded_statuses": list(EXCLUDED_PLACEMENT_STATUSES),
        },
    ).fetchall()


def derive_co_occurrence_pairs(
    session: Any, *, domain_key: str, skeleton: Any
) -> list[dict]:
    """同じ論文群に配置されている concept ペア（§4 ②）。

    戻り値の各要素は ``{from_id, from_label, to_id, to_label, documents}``。
    ``documents`` は ``{document_id, title}`` を ``(title, document_id)`` 昇順で
    並べたもの（**件数は載せない** — RE4。教員には論文タイトルを列挙する）。
    """
    domain = _clean(domain_key)
    concepts = concept_index(skeleton)
    if not domain or not concepts:
        return []
    edge_pairs = existing_edge_pairs(skeleton)

    # node_id -> {document_id: title}（骨格に無い node の配置は最初に落とす）
    by_node: dict[str, dict[str, str]] = {}
    for row in _fetch_placements(session, domain) or []:
        node_id = _clean(row[2])
        if node_id not in concepts:
            continue
        document_id = _clean(row[0])
        if not document_id:
            continue
        by_node.setdefault(node_id, {}).setdefault(document_id, _clean(row[1]))

    node_ids = sorted(by_node)
    out: list[dict] = []
    for i, left_id in enumerate(node_ids):
        for right_id in node_ids[i + 1 :]:
            left, right = schema.undirected_pair(left_id, right_id)
            if _is_excluded_pair(
                left, right, concepts=concepts, edge_pairs=edge_pairs
            ):
                continue
            shared = set(by_node[left_id]) & set(by_node[right_id])
            if len(shared) < schema.MIN_DOCUMENTS_FOR_EDGE:
                continue
            documents = sorted(
                (
                    {
                        "document_id": document_id,
                        "title": by_node[left_id].get(document_id, ""),
                    }
                    for document_id in shared
                ),
                key=lambda d: (d["title"], d["document_id"]),
            )
            out.append(
                {
                    "from_id": left,
                    "from_label": concepts[left]["label"],
                    "to_id": right,
                    "to_label": concepts[right]["label"],
                    "documents": documents,
                }
            )
    out.sort(key=lambda item: (item["from_id"], item["to_id"]))
    return out


# ---------------------------------------------------------------------------
# 3. 合成
# ---------------------------------------------------------------------------


def derive_edge_candidates(
    session: Any,
    *,
    domain_key: str,
    skeleton: Any,
    anchors: Sequence[Any] = (),
) -> list[dict]:
    """辺候補を毎回導出する（保存しない = RE6）。

    各候補の DTO キー（**未測定・非該当はキー自体を付けない** — RE4 / PR2 と同じ規律）::

        {
          "edge_key": str,          # 無向・版非依存
          "domain_key": str,
          "from_id": str, "from_label": str,
          "to_id": str,   "to_label": str,
          "origins": [str, ...],    # ソート済み（vector / co_occurrence）
          "nearness_label": str,    # vector 由来のときだけ
          "documents": [{"document_id", "title"}, ...],  # co_occurrence 由来のときだけ
          "skeleton_version": str,
        }

    教員の判断（``atlas_edge_decisions``）のマージは呼び出し側の責務
    （:func:`core.atlas_edges.store.merge_decisions_into`）。骨格が読めない・
    concept がゼロなら ``[]``（fail-closed）。
    """
    domain = _clean(domain_key)
    if not domain or not concept_index(skeleton):
        return []
    version = _clean(getattr(skeleton, "version", ""))

    merged: dict[tuple[str, str], dict] = {}

    def _slot(item: Mapping[str, Any]) -> dict:
        key = (item["from_id"], item["to_id"])
        entry = merged.get(key)
        if entry is None:
            entry = {
                "edge_key": schema.build_edge_key(domain, key[0], key[1]),
                "domain_key": domain,
                "from_id": key[0],
                "from_label": item["from_label"],
                "to_id": key[1],
                "to_label": item["to_label"],
                "origins": [],
                "skeleton_version": version,
            }
            merged[key] = entry
        return entry

    for item in derive_vector_pairs(skeleton, anchors):
        entry = _slot(item)
        entry["origins"].append(schema.ORIGIN_VECTOR)
        entry["nearness_label"] = item["nearness_label"]

    for item in derive_co_occurrence_pairs(
        session, domain_key=domain, skeleton=skeleton
    ):
        entry = _slot(item)
        entry["origins"].append(schema.ORIGIN_CO_OCCURRENCE)
        entry["documents"] = item["documents"]

    out = list(merged.values())
    for entry in out:
        entry["origins"] = sorted(set(entry["origins"]))
    out.sort(key=lambda entry: (entry["from_id"], entry["to_id"]))
    return out


__all__ = [
    "EXCLUDED_PLACEMENT_STATUSES",
    "NODE_KIND_CONCEPT",
    "concept_index",
    "derive_co_occurrence_pairs",
    "derive_edge_candidates",
    "derive_vector_pairs",
    "existing_edge_pairs",
]
