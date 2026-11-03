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

import json
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import text as sa_text

from core import document_refs
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
    """当該ドメインの live 配置（``document_id`` / タイトル / ``node_id`` / 論文の同一性の
    材料 / 配置の理由と逐語）。

    ``corpus_view._visible_placements`` と同じ形だが、**可視性ゲートを持たない**
    （教員のレビューキュー用。学習者向けの糸は vector 由来のみで、共起は使わない）。
    列順: ``document_id, title, node_id, source_url, filename, reason, evidence``。
    ``weight`` / ``confidence`` は SELECT しない（RE4）。
    """
    return session.execute(
        sa_text(
            """
            SELECT p.document_id::text,
                   COALESCE(NULLIF(d.title, ''), NULLIF(d.filename, ''), '') AS title,
                   p.node_id,
                   COALESCE(d.source_url, '') AS source_url,
                   COALESCE(d.filename, '') AS filename,
                   COALESCE(p.reason, '') AS reason,
                   p.evidence
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


def _row_cell(row: Any, index: int) -> Any:
    return row[index] if len(row) > index else None


def _first_evidence_quote(value: Any) -> str:
    """配置の ``evidence``（``[{"quote", "claim_id"}]`` の JSONB）から先頭の逐語引用。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return ""
    if isinstance(value, Mapping):
        value = [value]
    for item in value or ():
        if isinstance(item, Mapping):
            quote = _clean(item.get("quote"))
            if quote:
                return quote
    return ""


def derive_co_occurrence_pairs(
    session: Any, *, domain_key: str, skeleton: Any
) -> list[dict]:
    """同じ論文群に配置されている concept ペア（§4 ②）。

    戻り値の各要素は ``{from_id, from_label, to_id, to_label, documents}``。
    ``documents`` は ``{document_id, title[, endpoints]}`` を ``(title, document_id)`` 昇順で
    並べたもの（**件数は載せない** — RE4。教員には論文タイトルを列挙する）。

    - 反復の閾値は「論文」の数で数える（IK-0846）。同じ論文の重複取り込み（PDF 版と
      TeX 版など）は :func:`core.document_refs.representative_document_ids` で 1 本に畳み、
      読める題名を持つ取り込みを代表として 1 行にする。
    - ``endpoints`` は両端の配置が持つ理由と先頭の逐語引用（IK-0849。
      ``[{node_id, node_label, reason, evidence_quote}]``・端点の順は ``from`` → ``to``）。
      理由も逐語も無い端点は載せず、両端とも無ければキー自体を付けない。
      ``weight`` / ``confidence`` は運ばない（RE4）。
    """
    domain = _clean(domain_key)
    concepts = concept_index(skeleton)
    if not domain or not concepts:
        return []
    edge_pairs = existing_edge_pairs(skeleton)

    rows = [
        row
        for row in (_fetch_placements(session, domain) or [])
        if _clean(row[2]) in concepts and _clean(row[0])
    ]
    titles: dict[str, str] = {}
    for row in rows:
        titles.setdefault(_clean(row[0]), _clean(row[1]))
    representatives = document_refs.representative_document_ids(
        {
            "document_id": _clean(row[0]),
            "title": _clean(row[1]),
            "source_url": _row_cell(row, 3),
            "filename": _row_cell(row, 4),
        }
        for row in rows
    )

    # node_id -> {代表 document_id: {document_id(実際の取り込み): {reason, quote}}}
    by_node: dict[str, dict[str, dict[str, dict]]] = {}
    for row in rows:
        node_id = _clean(row[2])
        document_id = _clean(row[0])
        paper = representatives.get(document_id, document_id)
        slot = by_node.setdefault(node_id, {}).setdefault(paper, {})
        if document_id not in slot:
            slot[document_id] = {
                "reason": _clean(_row_cell(row, 5)),
                "evidence_quote": _first_evidence_quote(_row_cell(row, 6)),
            }

    def _endpoint(node_id: str, paper: str) -> dict | None:
        placements = by_node[node_id].get(paper) or {}
        # 代表の取り込みの配置を先に、無ければ同じ論文の別の取り込みの配置。
        ordered = [placements[paper]] if paper in placements else []
        ordered += [placements[d] for d in sorted(placements) if d != paper]
        for placement in ordered:
            if placement["reason"] or placement["evidence_quote"]:
                return {
                    "node_id": node_id,
                    "node_label": concepts[node_id]["label"],
                    "reason": placement["reason"],
                    "evidence_quote": placement["evidence_quote"],
                }
        return None

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
            documents = []
            for paper in shared:
                entry: dict[str, Any] = {
                    "document_id": paper,
                    "title": titles.get(paper, ""),
                }
                endpoints = [
                    e for e in (_endpoint(left, paper), _endpoint(right, paper)) if e
                ]
                if endpoints:
                    entry["endpoints"] = endpoints
                documents.append(entry)
            documents.sort(key=lambda d: (d["title"], d["document_id"]))
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
          "documents": [{"document_id", "title"[, "endpoints"]}, ...],  # co_occurrence 由来のときだけ
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

    # 近さの順位（derive_vector_pairs は近い順に並ぶ — 値ではなく順位だけを持つ）。
    nearness_rank: dict[tuple[str, str], int] = {}
    for rank, item in enumerate(derive_vector_pairs(skeleton, anchors)):
        entry = _slot(item)
        entry["origins"].append(schema.ORIGIN_VECTOR)
        entry["nearness_label"] = item["nearness_label"]
        nearness_rank[(item["from_id"], item["to_id"])] = rank

    for item in derive_co_occurrence_pairs(
        session, domain_key=domain, skeleton=skeleton
    ):
        entry = _slot(item)
        entry["origins"].append(schema.ORIGIN_CO_OCCURRENCE)
        entry["documents"] = item["documents"]

    out = list(merged.values())
    for entry in out:
        entry["origins"] = sorted(set(entry["origins"]))
    # 並び（IK-0887）: 論文の根拠（共起）を持つ組を先に、続いて説明文どうしの近さの順
    # （順位だけを使う。値は外へ出さない — RE4）。順位の無い組は (from, to) 昇順で後ろ。
    unranked = len(nearness_rank)
    out.sort(
        key=lambda entry: (
            0 if schema.ORIGIN_CO_OCCURRENCE in entry["origins"] else 1,
            nearness_rank.get((entry["from_id"], entry["to_id"]), unranked),
            entry["from_id"],
            entry["to_id"],
        )
    )
    return out


def derived_origins_for_keys(
    session: Any,
    *,
    domain_key: str,
    skeleton: Any,
    edge_keys: Iterable[str],
    anchors: Sequence[Any] = (),
) -> dict[str, list[str]]:
    """``edge_key -> origins``（**いま**候補として導出される組だけ。IK-0962）。

    採用の時点に出所（``mapping_justification``）を記録していない過去の判断に、
    いまの導出の出所を「いまの導出では」と時点を明示して添えるための読み取り。
    導出されない組はキー自体を付けない（推測で埋めない = KR4）。空入力では
    導出を走らせない（SQL も発行しない）。
    """
    keys = {_clean(k) for k in (edge_keys or ()) if _clean(k)}
    if not keys:
        return {}
    out: dict[str, list[str]] = {}
    for entry in derive_edge_candidates(
        session, domain_key=domain_key, skeleton=skeleton, anchors=anchors
    ):
        key = _clean(entry.get("edge_key"))
        if key in keys and entry.get("origins"):
            out[key] = list(entry["origins"])
    return out


def cap_for_review(
    candidates: Sequence[Mapping[str, Any]],
    *,
    keep_keys: Iterable[str] = (),
    max_per_node: int = schema.REVIEW_MAX_PER_NODE,
    max_total: int = schema.REVIEW_MAX_TOTAL,
) -> tuple[list[dict], bool]:
    """教員のレビュー一覧の上限と畳み（IK-0887）。``(表示する候補, 畳んだか)`` を返す。

    - 共起（論文の根拠）を持つ候補と ``keep_keys``（教員の判断がある候補）は常に残す
      （判断した候補を一覧から消さない — IK-0884 と同じ規律）。
    - 説明文どうしの近さだけの候補は、入力の順（:func:`derive_edge_candidates` の
      近さ順）に、1 ノードあたり ``max_per_node``・全体 ``max_total`` まで残す。
    - 畳んだ件数は返さない（真偽だけ — RE4）。
    """
    keep = {str(k or "") for k in keep_keys or () if str(k or "")}
    shown: list[dict] = []
    per_node: dict[str, int] = {}
    folded = False
    vector_only_shown = 0
    for candidate in candidates or ():
        item = dict(candidate)
        origins = set(item.get("origins") or ())
        if schema.ORIGIN_CO_OCCURRENCE in origins or str(item.get("edge_key") or "") in keep:
            shown.append(item)
            continue
        left = str(item.get("from_id") or "")
        right = str(item.get("to_id") or "")
        if (
            vector_only_shown >= max_total
            or per_node.get(left, 0) >= max_per_node
            or per_node.get(right, 0) >= max_per_node
        ):
            folded = True
            continue
        per_node[left] = per_node.get(left, 0) + 1
        per_node[right] = per_node.get(right, 0) + 1
        vector_only_shown += 1
        shown.append(item)
    return shown, folded


__all__ = [
    "EXCLUDED_PLACEMENT_STATUSES",
    "cap_for_review",
    "NODE_KIND_CONCEPT",
    "concept_index",
    "derive_co_occurrence_pairs",
    "derive_edge_candidates",
    "derive_vector_pairs",
    "existing_edge_pairs",
]
