---
id: IK-0565
title: "グラフ全体対話の grounding に同文ノードと双方向辺が重複し、内部 ID も残っていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [graph_review, deliberation_w]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    同文ノードを 1 行に畳み、双方向辺を 1 つの事実文にし、内部 ID を置換した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "同じ内容の項目を畳まずに LLM の入力へ並べる"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [single_point_fix]
  note: "同文ノードを 1 行に畳み、双方向辺を 1 つの事実文にし、内部 ID を置換した。"
  landed_in:
    - backend/core/deliberation/graph_dialogue.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: [IK-0528]
view_of: []
history: []
---

## 課題

グラフ全体対話の grounding に同文ノードと双方向辺が重複し、内部 ID も残っていた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

同文ノードを 1 行に畳み、双方向辺を 1 つの事実文にし、内部 ID を置換した。

## 一般化

同じ内容の項目を畳まずに LLM の入力へ並べる。
