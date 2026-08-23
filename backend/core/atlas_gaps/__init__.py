"""カテゴリギャップ候補（Category Gap Candidates）core パッケージ。

論文の解析が「この地図では言い表せなかった主題」を申告したとき、それを**信号**として
積み、複数の論文で反復したものだけを**候補**として教員のレビューに浮上させ、教員が
採用したものを決定論 JSON Patch で次版の下書きへ**1クリックで取り込める**ようにする層。
設計の正本は ``docs/features/category_gap_candidates_design.md``（裁定は §4）。

各モジュール:
    schema   — 層 / 状態 / 反復閾値 / cluster_key 導出の語彙の正本（§5.2。
               migration 066 の CHECK と一致）
    store    — ``landscape_gap_signals`` / ``atlas_gap_decisions`` の DB プリミティブ
               （§5.2。候補は行ではなく :func:`store.derive_candidates` の読み時導出）
    patching — 骨格 draft への決定論 JSON Patch 生成（§5.5。LLM 不使用・**add のみ**）

規約:

- **開発ルール2**: 本パッケージは FastAPI も LLM SDK も import しない。権限ゲート・
  HTTP 変換・監査の呼び出し方は API 層（後続 Wave の ``api/routes/...``）の責務。
- **KN-3 / AB4 確定は人間**: AI が書けるのは信号（``landscape_gap_signals``）だけで、
  判断（``atlas_gap_decisions``）は教員の明示操作でしか動かない。骨格 draft へ
  書き込む経路は本パッケージに**存在しない**（patching は patch を返すだけ）。
- **LS7 地図の安定性**: ``atlas_skeletons`` への INSERT / UPDATE を書かない
  （ガードレールテストが構造的に検査する）。
- **P4 / AB3**: 行削除 API を作らない。見送りも状態遷移で保持する。
- **LS5 / PN-4 数値を見せない**: ``confidence`` は DB 界面までで、外へ出るのは段階
  ラベルのみ。「該当論文 N 件」のような集計数値は作らない（支持論文はリストで示す）。
"""

from core.atlas_gaps.schema import (
    DECISION_STATUSES,
    DECISION_STATUS_ACCEPTED,
    DECISION_STATUS_CANDIDATE,
    DECISION_STATUS_DISMISSED,
    DECISION_STATUS_MERGED,
    GAP_LAYERS,
    GAP_LAYER_CONCEPT,
    GAP_LAYER_REGION,
    MIN_DOCUMENTS_FOR_CANDIDATE,
    SIGNAL_STATUSES,
    SIGNAL_STATUS_ACTIVE,
    SIGNAL_STATUS_SUPERSEDED,
    build_cluster_key,
    confidence_label,
    decision_status_label,
    layer_label,
    normalize_label,
)

__all__ = [
    "DECISION_STATUSES",
    "DECISION_STATUS_ACCEPTED",
    "DECISION_STATUS_CANDIDATE",
    "DECISION_STATUS_DISMISSED",
    "DECISION_STATUS_MERGED",
    "GAP_LAYERS",
    "GAP_LAYER_CONCEPT",
    "GAP_LAYER_REGION",
    "MIN_DOCUMENTS_FOR_CANDIDATE",
    "SIGNAL_STATUSES",
    "SIGNAL_STATUS_ACTIVE",
    "SIGNAL_STATUS_SUPERSEDED",
    "build_cluster_key",
    "confidence_label",
    "decision_status_label",
    "layer_label",
    "normalize_label",
]
