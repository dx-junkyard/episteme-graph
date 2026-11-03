---
id: IK-0563
title: "ゼミ前ブリーフの statement / fact_line に内部 ID（claim_span_*・eq_* 等）がそのまま出ていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [seminar_brief, doubt_d]
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
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    `_ReadableLabels` で内部 ID を表示ラベルに置き換える。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "内部 ID を表示ラベルへ解決せずに事実文へ埋め込む"
pattern: id-namespace-conflated
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [single_point_fix]
  note: "`_ReadableLabels` で内部 ID を表示ラベルに置き換える。"
  landed_in:
    - backend/core/doubt/seminar_brief.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: [IK-0531]
view_of: []
history: []
---

## 課題

ゼミ前ブリーフの statement / fact_line に内部 ID（claim_span_*・eq_* 等）がそのまま出ていた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

`_ReadableLabels` で内部 ID を表示ラベルに置き換える。

## 一般化

内部 ID を表示ラベルへ解決せずに事実文へ埋め込む。
