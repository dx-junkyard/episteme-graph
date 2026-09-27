---
id: IK-0364
title: 学習チャットの RAG 検索で質問文を埋め込む呼び出しが usage_context の外で走るため、U層の記録に feature=unattributed で積まれ、チャット機能の消費として集計されない
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-27
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習チャット・discuss で質問に答える（RAG 検索 → 生成）
  layers: [rag_chat, usage_metering_u]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [ordering]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    統制軸: `_learning_chat_core` は `search_chunks_with_metadata`（内部で `generate_embeddings`）を先に呼び、
    `with usage_context(_chat_feature, ...)` は生成の直前で開く（ordering。帰属の文脈がゲートの後ろにある）。
    接続軸: feature という条件が検索の呼び出しに運ばれない（condition）。確認: 砂場の `llm_usage_events` で
    `learning:chat_discuss` の chat 行と同時刻に `unattributed` の embedding 行（document 直付け discuss を
    2 回叩いて 2 行）。教材投入時にも unattributed の embedding が 2 行（別経路・要確認）。
generalization:
  level: repo_pattern
  general_form: 帰属の文脈を開く位置が、帰属したい最初の外部呼び出しより後ろにある
pattern: gate-position-wrong
discovery:
  perspective: [data_inspection]
  note: 審判 E が砂場 DB の `llm_usage_events` を feature 別に数えて unattributed を見つけ、コードで検索と文脈の順序を確かめた。
resolution:
  perspective: [order_and_budget, guardrail_fix]
  note: >-
    検索の直前で当該ターンの feature を同じ順序（cycle > discuss > casual > chat）で先取りし、検索を usage_context の内側で呼ぶ（order_and_budget）。順序と一致をソーステキストで固定するテストを足した（guardrail_fix）。分岐の正本を 1 箇所にまとめる案は既存ガードレールが後段の分岐文を固定しているため採らなかった。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_llm_usage_attribution.py
  verification:
    methods: [reproduction_rerun, docker_e2e, guardrail]
    unverified:
      - 教材投入時の unattributed embedding 2 行（別経路・未追跡）
      - ストリーミング経路（LEARNING_CHAT_STREAMING_ENABLED=true）での帰属
related: []
view_of: []
history: []
---

## 課題

学習チャットの質問文の埋め込み（RAG 検索）が U層で `unattributed` になる。チャット機能ごとの消費が過小に見える。

## 発見の観点

砂場 DB の U層イベントの feature 別集計。

## 解決の観点

検索を feature の内側へ移した。砂場を再ビルドし、同じ document 直付け discuss を台本モードで再演すると
embedding 行が `learning:chat_discuss` に帰属した（是正前の run では `unattributed`）。

## 一般化

ゲート（文脈）の位置が対象の呼び出しより後ろにある型。
