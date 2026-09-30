---
id: IK-0567
title: "論文層で表の見出し行を章見出しと誤認し、配下の要素の親が失われていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [graph_paper_layer]
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
    `_looks_like_table_header` で表の見出しを除き、`_reattach_orphan_parents` で親を付け直す。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "見出しらしい書式だけで構造上の役割を決める"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [single_point_fix]
  note: "`_looks_like_table_header` で表の見出しを除き、`_reattach_orphan_parents` で親を付け直す。"
  landed_in:
    - backend/core/graph_paper_layer/builder.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: [IK-0526]
view_of: []
history: []
---

## 課題

論文層で表の見出し行を章見出しと誤認し、配下の要素の親が失われていた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

`_looks_like_table_header` で表の見出しを除き、`_reattach_orphan_parents` で親を付け直す。

## 一般化

見出しらしい書式だけで構造上の役割を決める。
