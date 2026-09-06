"""分野マップのベクトル係留層（Atlas Vector Anchoring, VA層）。

正本: ``docs/features/atlas_vector_anchoring_design.md``（不変条項 VA1〜VA9 は §2）。

骨格（atlas 凍結版）の region / concept に**プロトタイプベクトル**（label + 確定別名 +
確定配置の evidence 引用の合成テキストの埋め込み）を与え、論文（既に pgvector 空間に
住んでいるチャンク埋め込み）と同じ空間で扱えるようにする層。3つの用途がある:

- 配置の前段絞り込み（``query.prefilter_domains``、§6）
- 別名レジストリ（``store`` の alias 群 + ``annotate.annotate_gap_clusters``、§7）
- 着地予測（``query.landing_for_vector``、§8）

構成（FastAPI を import しない — core 層の規約）:

- ``schema``   合成テキストの正本と語彙定数
- ``store``    migration 074 の2表への DB 読み書き（別名は status 遷移のみ）
- ``builder``  現行凍結版のアンカー構築（差分のみ埋め込み → 全置換保存）
- ``query``    純計算（cosine / 近傍 / 着地 / 前段絞り込み）
- ``annotate`` ギャップ候補への近傍注記（読み時導出・保存しない・fail-soft）

**ベクトルは候補生成器であり、確定は常に人間**（VA1）。本パッケージから
``atlas_skeletons`` へ書き込む経路は存在しない（VA9）。
"""

from core.atlas_vectors.annotate import annotate_gap_clusters
from core.atlas_vectors.builder import (
    anchors_with_labels,
    build_anchor_embeddings,
    reset_daily_counter,
)
from core.atlas_vectors.query import (
    cosine_similarity,
    landing_for_vector,
    nearest_anchors,
    prefilter_domains,
)
from core.atlas_vectors.schema import (
    ALIAS_SOURCES,
    ALIAS_STATUSES,
    AUDIT_ACTIONS,
    NODE_KINDS,
    build_anchor_source_text,
    normalize_label,
    source_hash,
)

__all__ = [
    "ALIAS_SOURCES",
    "ALIAS_STATUSES",
    "AUDIT_ACTIONS",
    "NODE_KINDS",
    "anchors_with_labels",
    "annotate_gap_clusters",
    "build_anchor_embeddings",
    "build_anchor_source_text",
    "cosine_similarity",
    "landing_for_vector",
    "nearest_anchors",
    "normalize_label",
    "prefilter_domains",
    "reset_daily_counter",
    "source_hash",
]
