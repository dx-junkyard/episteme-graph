"""分野の地図のアンカー（concept プロトタイプ）同士の近さの分布を読む（IK-0887 の校正材料）。

関係（辺）の候補の vector 由来は ``ANCHOR_NEARNESS_THRESHOLD_NEAR`` 以上の組だけを通す。
この閾値はラベル×ラベル（gap 注記）の比較で校正した値で、プロトタイプ同士の分布で
確かめていない（IK-0887 の仮説）。本スクリプトは**保存済みベクトルを読むだけ**で、
embedding API を呼ばず、DB に書き込まない（RE6 / VA3）。閾値を変えるときは、出力の分位を
``docs/features/atlas_vector_anchoring_design.md`` §9 に実測として記録してから変える。

使い方（DB 接続は ``core.postgres`` の環境変数に従う）::

    cd backend && .venv/bin/python -m scripts.atlas_anchor_pair_distribution <domain_key>

出力は分位（最小・10%・25%・50%・75%・90%・最大）と、現在の閾値を超える組の割合
（同じ領域の中・既存の辺は除く = 候補の母集合と同じ条件）。組の名前は出さない。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def pair_similarities(skeleton, anchors) -> list[float]:
    """候補の母集合と同じ除外条件で、concept の組ごとの cosine を返す（純関数）。"""
    from core.atlas_edges import derive
    from core.atlas_edges import schema
    from core.atlas_vectors.query import cosine_similarity

    concepts = derive.concept_index(skeleton)
    edge_pairs = derive.existing_edge_pairs(skeleton)
    usable = [
        a for a in anchors or ()
        if str(getattr(a, "node_kind", "")) == derive.NODE_KIND_CONCEPT
        and str(getattr(a, "node_id", "")) in concepts
        and getattr(a, "vector", None)
    ]
    usable.sort(key=lambda a: str(a.node_id))
    out: list[float] = []
    for i, left in enumerate(usable):
        for right in usable[i + 1:]:
            a, b = schema.undirected_pair(str(left.node_id), str(right.node_id))
            if derive._is_excluded_pair(a, b, concepts=concepts, edge_pairs=edge_pairs):
                continue
            value = cosine_similarity(left.vector, right.vector)
            if value is not None:
                out.append(float(value))
    return out


def quantiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)

    def at(q: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return ordered[index]

    return {name: at(q) for name, q in (
        ("min", 0.0), ("p10", 0.1), ("p25", 0.25), ("p50", 0.5),
        ("p75", 0.75), ("p90", 0.9), ("max", 1.0),
    )}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    domain_key = argv[1]
    from core import atlas_store
    from core.atlas_vectors.builder import anchors_with_labels
    from core.label_vocab import ANCHOR_NEARNESS_THRESHOLD_NEAR
    from core.postgres import get_session

    session = get_session()
    try:
        skeleton = atlas_store.load_learner_skeleton(domain_key, session)
        if skeleton is None:
            print("凍結済みの骨格がありません")
            return 1
        anchors, version = anchors_with_labels(session, domain_key, skeleton.version)
    finally:
        session.close()
    values = pair_similarities(skeleton, anchors)
    print(f"domain={domain_key} version={version or skeleton.version} pairs={len(values)}")
    for name, value in quantiles(values).items():
        print(f"  {name}: {value:.3f}")
    if values:
        above = sum(1 for v in values if v >= ANCHOR_NEARNESS_THRESHOLD_NEAR)
        print(f"  threshold={ANCHOR_NEARNESS_THRESHOLD_NEAR} above_ratio={above / len(values):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
