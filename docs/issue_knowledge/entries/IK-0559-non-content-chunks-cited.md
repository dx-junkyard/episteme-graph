---
id: IK-0559
title: "謝辞・題名ブロック・章立ての概要のチャンクが出典として採用されていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat, pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    `non_content_chunk_reason` に acknowledgments・title_block・outline の 3 種を足した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "検索スコアだけで採否を決め、内容を持たない区画を除かない"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [single_point_fix]
  note: "`non_content_chunk_reason` に acknowledgments・title_block・outline の 3 種を足した。"
  landed_in:
    - backend/api/services.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: [IK-0513]
view_of: []
history: []
---

## 課題

謝辞・題名ブロック・章立ての概要のチャンクが出典として採用されていた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

`non_content_chunk_reason` に acknowledgments・title_block・outline の 3 種を足した。

## 一般化

検索スコアだけで採否を決め、内容を持たない区画を除かない。
