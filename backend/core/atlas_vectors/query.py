"""分野マップのベクトル係留層 — 純計算（DB は store 経由・LLM 非接触）。

正本: ``docs/features/atlas_vector_anchoring_design.md`` §6 / §8 / §9。

本モジュールは**純関数のみ**。ベクトルとアンカーの一覧を受け取り、cosine を計算して
段階ラベル（正本は ``core.label_vocab`` — 着地予測・新しい面は論文テキスト×アンカーの
言語間レジーム用 ``ANCHOR_LANDING_SCALE``、gap 近傍注記はラベル×ラベル用
``ANCHOR_NEARNESS_SCALE``）へ変換する。

不変条項:

- **VA2 数値非表示**: cosine の生値をこのモジュールの**外へ出す DTO に載せない**。
  :func:`cosine_similarity` / :func:`nearest_anchors` は内部計算用に生値を返すが、
  DTO を組む :func:`landing_for_vector` は段階ラベルしか載せない。閾値・ラベル文字列は
  ``core.label_vocab`` からのみ引き、ここに直書きしない。
- **VA8 閉世界の正直さ**: 着地予測は「この骨格（版N）の中で最も近い」の言明のみ。
  版の刻印は呼び出し側が付ける（骨格版を知っているのは呼び出し側）。
- **VA4 fail-soft / 慎重側**: 前段絞り込みは**ベクトルを持たない concept を落とさない**
  （比較できないものを捨てない）。region は常に全提示。centroid なし / アンカーなし /
  ``top_k <= 0`` は無加工で素通し。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Optional, Sequence

from core.label_vocab import (
    ANCHOR_LANDING_SCALE,
    ANCHOR_LANDING_THRESHOLD_MID,
    ANCHOR_LANDING_THRESHOLD_NEAR,
)

#: 着地予測・近傍注記に載せる region / concept の種別語彙（骨格側の値と一致）。
_KIND_REGION = "region"


def cosine_similarity(
    left: Optional[Sequence[float]], right: Optional[Sequence[float]]
) -> Optional[float]:
    """cosine 類似度（次元不一致・ゼロベクトル・空は ``None`` = 未測定）。

    戻り値は**この層の内部でだけ**使う（DTO へ出さない — VA2）。
    """
    if not left or not right or len(left) != len(right):
        return None
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0.0 or right_norm <= 0.0:
        return None
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


def nearest_anchors(
    vector: Optional[Sequence[float]],
    anchors: Iterable[Any],
    *,
    limit: int = 3,
) -> list[tuple[Any, float]]:
    """``vector`` に近いアンカーを類似度降順で最大 ``limit`` 件返す。

    未測定（cosine が ``None``）のアンカーは**含めない** — 「測れなかった」を
    「遠い」に化けさせないため（PR2 と同じ規律）。同点は入力順を保つ安定ソート。

    Returns:
        ``[(anchor, similarity), ...]``。生値を含むので**内部利用限定**。
    """
    if not vector or limit <= 0:
        return []
    scored: list[tuple[int, float, Any]] = []
    for index, anchor in enumerate(anchors or ()):
        similarity = cosine_similarity(vector, getattr(anchor, "vector", None))
        if similarity is None:
            continue
        scored.append((index, similarity, anchor))
    scored.sort(key=lambda row: (-row[1], row[0]))
    return [(row[2], row[1]) for row in scored[:limit]]


def landing_for_vector(
    vector: Optional[Sequence[float]],
    anchors: Iterable[Any],
) -> Optional[dict]:
    """「この骨格の中で最も近いノード」の事実 dict（生スコアなし — VA2）。

    最下帯（:data:`ANCHOR_LANDING_THRESHOLD_MID` 未満）は ``None`` を返す
    （「なんとなく関連」を出さない — 設計書 §9 / help_kb の保守的足切りと同じ思想）。
    アンカー不在・未測定も ``None``（キー自体を付けない = VA4）。

    Returns:
        ``{"node_id", "node_kind", "node_label", "region_label", "nearness_label"}``。
        ``skeleton_version`` は**呼び出し側が付ける**（骨格版を知っているのは
        呼び出し側であり、ここで捏造しない — VA8）。
    """
    best = nearest_anchors(vector, anchors, limit=1)
    if not best:
        return None
    anchor, similarity = best[0]
    if similarity < ANCHOR_LANDING_THRESHOLD_MID:
        return None
    return {
        "node_id": getattr(anchor, "node_id", ""),
        "node_kind": getattr(anchor, "node_kind", ""),
        "node_label": getattr(anchor, "label", "") or getattr(anchor, "node_id", ""),
        "region_label": getattr(anchor, "region_label", "") or "",
        "nearness_label": ANCHOR_LANDING_SCALE.label_for(similarity),
    }


def new_facet_labels(
    vector: Optional[Sequence[float]],
    anchors: Iterable[Any],
    exclude_node_ids: Optional[Iterable[str]] = None,
    *,
    limit: int = 2,
) -> list[str]:
    """候補は近いのに ``exclude_node_ids`` に無いアンカーのラベル（生スコアなし — VA2）。

    論文レーダー（``paper_radar_design.md``）の「新しい面」チップの材料。
    ``exclude_node_ids`` には**起点論文が既に配置されているノード**を渡す想定で、
    そこに現れないアンカーだけが「この候補が持ち込みそうな面」として残る。

    規律:

    - **最上位帯のみ**（:data:`ANCHOR_LANDING_THRESHOLD_NEAR` 以上）。中位帯まで
      拾うと「なんとなく関連」が新しい面として並ぶ（:func:`landing_for_vector` が
      最下帯を切るのと同じ思想の、さらに慎重な足切り — 設計書 §9）。
    - **未測定（cosine が ``None``）のアンカーは含めない**（:func:`nearest_anchors`
      の規律をそのまま継承。「測れなかった」を「近い」に化けさせない — PR2）。
    - 返すのは**ラベル文字列だけ**（cosine の生値・件数・node_id を外へ出さない — VA2）。
      ラベルが空のアンカーは ``node_id`` で代替し、重複ラベルは1つに畳む。
    - ベクトル不在・アンカー不在・``limit <= 0`` は空リスト（呼び出し側はキー自体を
      付けない = VA4）。

    Returns:
        近い順・最大 ``limit`` 件のラベル。
    """
    if not vector or int(limit or 0) <= 0:
        return []
    items = list(anchors or ())
    if not items:
        return []
    excluded = {str(node_id or "") for node_id in (exclude_node_ids or ())}

    out: list[str] = []
    seen: set[str] = set()
    for anchor, similarity in nearest_anchors(vector, items, limit=len(items)):
        if similarity < ANCHOR_LANDING_THRESHOLD_NEAR:
            # 近い順に並んでいるので、ここから先は全て帯の外。
            break
        node_id = str(getattr(anchor, "node_id", "") or "")
        if node_id in excluded:
            continue
        label = str(getattr(anchor, "label", "") or node_id).strip()
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
        if len(out) >= int(limit):
            break
    return out


def prefilter_domains(
    centroid: Optional[Sequence[float]],
    domains: Sequence[Mapping[str, Any]],
    anchors_by_domain: Mapping[str, Sequence[Any]],
    *,
    top_k: int,
) -> tuple[list[dict], dict]:
    """配置の前段絞り込み（設計書 §6）。

    ``domains`` は ``core/landscape/builder.py::collect_placement_domains()`` の返り値
    （各要素 ``{domain_key, domain_name, skeleton_version, nodes: [{node_id, label,
    kind, region_id}]}``）。**入力を変更しない**（コピーを返す）。

    規則:

    - **region ノードは常に全提示**（gap 検出の親 region 選択肢を狭めない）。
    - concept ノードは centroid との cosine 降順で上位 ``top_k`` 件だけ残す。
    - **ベクトルを持たない concept は残す**（比較不能なものを落とさない — 慎重側）。
      そのため実際に提示されるノード数は ``top_k`` を超えうる。
    - アンカーベクトルが1件も無いドメインは**無加工で素通し**。
    - ``centroid`` が無い / ``top_k <= 0`` は全ドメインを素通し。

    Returns:
        ``(domains, facts)``。間引きが起きたドメインの dict には
        ``prefiltered: True`` を立てる。``facts`` は
        ``{"applied": bool, "omitted": int, "domains": {domain_key: omitted}}``
        で、**間引いた数を必ず記録する**（silent truncation 禁止 — VA7）。
    """
    items = [dict(d) for d in (domains or [])]
    empty_facts = {"applied": False, "omitted": 0, "domains": {}}
    if not items or not centroid or int(top_k or 0) <= 0:
        return items, dict(empty_facts)

    limit = int(top_k)
    omitted_by_domain: dict[str, int] = {}
    total_omitted = 0

    out: list[dict] = []
    for domain in items:
        domain_key = str(domain.get("domain_key") or "")
        anchors = list(anchors_by_domain.get(domain_key) or [])
        nodes = [dict(n) for n in (domain.get("nodes") or [])]
        if not anchors or not nodes:
            # アンカー不在は素通し（VA4）。
            domain["nodes"] = nodes
            out.append(domain)
            continue

        vectors = {
            str(getattr(a, "node_id", "")): getattr(a, "vector", None) for a in anchors
        }

        # 位置（nodes 内の index）で判定し、最後は元の並び
        # （region → その配下 concept の交互）をそのまま保って再構成する。
        measurable: list[tuple[int, float]] = []
        for index, node in enumerate(nodes):
            if str(node.get("kind") or "") == _KIND_REGION:
                continue  # region は常に全提示
            similarity = cosine_similarity(
                centroid, vectors.get(str(node.get("node_id") or ""))
            )
            if similarity is None:
                continue  # 比較できない概念は落とさない（慎重側 — 設計書 §6）
            measurable.append((index, similarity))

        if len(measurable) <= limit:
            # 絞り込む必要が無い = 無加工（prefiltered フラグも立てない）。
            domain["nodes"] = nodes
            out.append(domain)
            continue

        measurable.sort(key=lambda row: (-row[1], row[0]))
        dropped = {row[0] for row in measurable[limit:]}
        omitted = len(dropped)

        domain["nodes"] = [n for i, n in enumerate(nodes) if i not in dropped]
        domain["prefiltered"] = True
        out.append(domain)

        if omitted:
            omitted_by_domain[domain_key] = omitted
            total_omitted += omitted

    facts = {
        "applied": bool(omitted_by_domain),
        "omitted": total_omitted,
        "domains": omitted_by_domain,
    }
    return out, facts


__all__ = [
    "cosine_similarity",
    "landing_for_vector",
    "nearest_anchors",
    "new_facet_labels",
    "prefilter_domains",
]
