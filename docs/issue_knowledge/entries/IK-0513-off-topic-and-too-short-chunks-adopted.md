---
id: IK-0513
title: "表示中トピックの論文と無関係なチャンクや、見出しだけの短すぎるチャンクが出典として採用されていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/backend/rag-chat.md
feature_context:
  realizing: "学習チャットの回答に教材の該当箇所を根拠として付ける"
  layers: [rag_chat]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    _drop_off_topic_chunks でトピックの論文外を落とし、non_content_chunk_reason に too_short を足した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "検索結果を採用する前に、対象範囲と中身の実質を検査しない"
pattern: scope-widened-silently
discovery:
  perspective: [data_inspection]
  note: "findings の出典に別論文の断片・見出しだけのチャンクが並んだ。"
resolution:
  perspective: [single_point_fix]
  note: "_drop_off_topic_chunks でトピックの論文外を落とし、non_content_chunk_reason に too_short を足した。"
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_learning_citation_wave15.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

無関係・中身の無いチャンクが出典になっていた。

## 発見の観点

findings の出典に別論文の断片・見出しだけのチャンクが並んだ。

## 解決の観点

_drop_off_topic_chunks でトピックの論文外を落とし、non_content_chunk_reason に too_short を足した。

## 一般化

検索結果を採用する前に、対象範囲と中身の実質を検査しない。
