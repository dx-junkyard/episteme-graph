"""知識ランドスケープ（Knowledge Landscape）core パッケージ。

論文（document）を分野の**基準地図**（``atlas_skeletons`` の凍結版）のどこに
どの観点で位置づけるかを積む配置層。設計の正本は
``docs/features/knowledge_landscape_design.md``（不変条項 LS1〜LS10 は §0）。

各モジュール:
    schema     — 観点 / 状態 / 出所 / 段階ラベルの語彙の正本（§8。migration 065 の CHECK と一致）
    store      — ``landscape_placements`` の DB プリミティブ（§5 の再解析セマンティクス）
    projection — 教員 DTO / 学習者 DTO への投影（§9。生数値を出さない）
    builder    — 骨格列挙 → agent 実行 → 永続化（§7。パイプラインステージと手動再提案の共通実体）

規約:

- **開発ルール2**: 本パッケージは FastAPI を import しない。権限ゲート
  （``resolve_document_access`` / ``get_accessible_course_data``）・監査記帳は
  API 層（``api/routes/landscape.py``）の責務。
- **LS2 A層非改変**: A層成果（``theory_components`` / ``theory_claims`` / artifacts）は
  読むだけで、列を足さない。配置は専用テーブルに積む。
- **LS3 candidate-only**: ``store`` が書く AI 由来の行は常に ``status='inferred'``。
  ``confirmed`` へ動かせるのは教員の明示操作（``store.update_status``）だけ。
- **LS5 数値を見せない**: ``weight`` の生値は DB 界面までで、DTO は段階ラベルのみ。
- **store のセッション規約**: 各関数は第1引数に session を受け取り commit / close しない
  （呼び出し側が1トランザクションとして束ねる）。
"""

from core.landscape.projection import (
    admin_placement_dto,
    learner_landscape_dto,
    skeleton_node_index,
)
from core.landscape.schema import (
    LEARNER_VISIBLE_STATUSES,
    LIVE_STATUSES,
    NODE_KINDS,
    PERSPECTIVES,
    PERSPECTIVE_LABELS,
    PROVENANCES,
    STATUSES,
    STATUS_CONFIRMED,
    STATUS_INFERRED,
    STATUS_REJECTED,
    STATUS_REVIEW_REQUIRED,
    STATUS_SUPERSEDED,
    WEIGHT_LABELS,
    perspective_label,
    provenance_label,
    status_label,
    weight_label,
)

__all__ = [
    "LEARNER_VISIBLE_STATUSES",
    "LIVE_STATUSES",
    "NODE_KINDS",
    "PERSPECTIVES",
    "PERSPECTIVE_LABELS",
    "PROVENANCES",
    "STATUSES",
    "STATUS_CONFIRMED",
    "STATUS_INFERRED",
    "STATUS_REJECTED",
    "STATUS_REVIEW_REQUIRED",
    "STATUS_SUPERSEDED",
    "WEIGHT_LABELS",
    "admin_placement_dto",
    "learner_landscape_dto",
    "perspective_label",
    "provenance_label",
    "skeleton_node_index",
    "status_label",
    "weight_label",
]
