---
id: IK-0566
title: "主グラフのノードが英語の stage 名と切り詰めた説明しか持たず、日本語の表示名が無かった"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [graph_review]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    読み時に日本語の `display_label` を付け（`_attach_main_stage_display_labels`）、description を全文で渡す。#308 のラベル規律（graph_json）は非改変。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "内部語彙のラベルを表示ラベルとして流用し、表示用の訳を足さない"
pattern: last-mile-missing
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [single_point_fix]
  note: "読み時に日本語の `display_label` を付け（`_attach_main_stage_display_labels`）、description を全文で渡す。#308 のラベル規律（graph_json）は非改変。"
  landed_in:
    - backend/api/routes/theory_components.py
    - frontend/public/js/admin-graph-review.js
    - frontend/public/admin.html
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: [IK-0546]
view_of: []
history: []
---

## 課題

主グラフのノードが英語の stage 名と切り詰めた説明しか持たず、日本語の表示名が無かった。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

読み時に日本語の `display_label` を付け（`_attach_main_stage_display_labels`）、description を全文で渡す。#308 のラベル規律（graph_json）は非改変。

## 一般化

内部語彙のラベルを表示ラベルとして流用し、表示用の訳を足さない。
