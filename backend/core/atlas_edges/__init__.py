"""分野マップの関係表示（Relation Edges, RE層）core パッケージ。

正本: ``docs/features/atlas_relation_edges_design.md``（不変条項 RE1〜RE8 は §2）。
親: ``docs/architecture/field_map_display_principles_2026-08-29.md`` の原則①′
「地形は人間・関係は離散の辺」。

骨格の辺（``SkeletonEdge``: adjacent / depends / related）を、VA層の**保存済み**
アンカーベクトルと配置データから**候補として**提案し、教員の確定を経て既存の
draft/freeze の弁を通す層。併せて、確定前の推定関係を点線レイヤー（推定の糸）として
重ねるためのデータを読み時に導出する。

各モジュール:
    schema   — edge_key（無向・版非依存）/ 出所 / 判断状態 / 上限の語彙の正本（§4。
               migration 076 の CHECK と一致）
    derive   — 辺候補の読み時導出（§4。vector + co_occurrence・**保存しない**）
    store    — ``atlas_edge_decisions`` の DB プリミティブ（§4。遷移は
               ``core/candidate_flow.py`` の CandidateFlow 経由 = 本番初適用）
    patching — 骨格 draft への決定論 JSON Patch 生成（§4。LLM 不使用・**add のみ**）
    threads  — 推定の糸（§6。vector 由来のみ・上限付き・見送りを除外・fail-soft）

規約:

- **開発ルール2**: 本パッケージは FastAPI も LLM SDK も import しない。権限ゲート・
  HTTP 変換・監査記帳の呼び出し方は API 層の責務（監査 callable は注入で受ける）。
- **RE6 embedding を呼ばない**: 候補生成は保存済みベクトルと配置行の読みだけ
  （学習者経路から呼ばれても外部 API に触れない）。
- **RE3 確定は人間**: AI / サーバが ``atlas_skeletons`` を書く経路は存在しない
  （patching は patch を返すだけ・反映は教員の既存 PUT）。
- **RE5 情報を落とさない**: 行削除 API を作らない。見送りも状態遷移で保持する。
- **RE4 数値を見せない**: cosine の生値・共起件数を外へ出さない（段階ラベルと
  論文タイトルの列挙のみ）。
"""

from core.atlas_edges.schema import (
    DECISION_STATUSES,
    DECISION_STATUS_ACCEPTED,
    DECISION_STATUS_CANDIDATE,
    DECISION_STATUS_DISMISSED,
    MIN_DOCUMENTS_FOR_EDGE,
    ORIGINS,
    ORIGIN_CO_OCCURRENCE,
    ORIGIN_VECTOR,
    THREADS_MAX_PER_NODE,
    THREADS_MAX_TOTAL,
    build_edge_key,
    decision_status_label,
    edge_key_domain_prefix,
    parse_edge_key,
    undirected_pair,
)

__all__ = [
    "DECISION_STATUSES",
    "DECISION_STATUS_ACCEPTED",
    "DECISION_STATUS_CANDIDATE",
    "DECISION_STATUS_DISMISSED",
    "MIN_DOCUMENTS_FOR_EDGE",
    "ORIGINS",
    "ORIGIN_CO_OCCURRENCE",
    "ORIGIN_VECTOR",
    "THREADS_MAX_PER_NODE",
    "THREADS_MAX_TOTAL",
    "build_edge_key",
    "decision_status_label",
    "edge_key_domain_prefix",
    "parse_edge_key",
    "undirected_pair",
]
