---
id: IK-0554
title: "部品の文脈の依存先が名前だけの項目で navigable:false になり、隣の部品へ辿れなかった"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [learner_experience_b, deliberation_w]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    依存先を同一論文の live component に解決し（`_dependency_lane_items`・`_merge_lane`）、辿れる項目にした。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "参照先の名前は持っているのに、同じ文書の実体行へ解決せず辿れない項目として出す"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [carry_through]
  note: "依存先を同一論文の live component に解決し（`_dependency_lane_items`・`_merge_lane`）、辿れる項目にした。"
  landed_in:
    - backend/core/component_context.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

部品の文脈の依存先が名前だけの項目で navigable:false になり、隣の部品へ辿れなかった。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

依存先を同一論文の live component に解決し（`_dependency_lane_items`・`_merge_lane`）、辿れる項目にした。

## 一般化

参照先の名前は持っているのに、同じ文書の実体行へ解決せず辿れない項目として出す。
