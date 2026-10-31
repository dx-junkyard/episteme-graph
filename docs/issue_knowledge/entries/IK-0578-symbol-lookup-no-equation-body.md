---
id: IK-0578
title: "記号 lookup が定義の位置の事実文だけを返し、式本文を返さないので定義に辿れない"
status: open
recorded_at: 2026-09-30
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [concept_registry, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: low
    structure: low
    connection: low
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    設計判断（P3-5 は位置の事実文まで）。式本文の返却は未決。 原因の性質で座標を置いた（症状の場所ではなく）。 未解決のため座標は暫定。
generalization:
  level: general
  general_form: "所在の事実文だけを返し、所在の中身へ辿る手段を渡さない"
pattern: last-mile-missing
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [pending]
  note: "未解決。第 16 波では是正していない。"
  landed_in: []
related: [IK-0518]
view_of: []
history: []
---

## 課題

記号 lookup が定義の位置の事実文だけを返し、式本文を返さないので定義に辿れない。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

未解決。設計判断（P3-5 は位置の事実文まで）。式本文の返却は未決。

## 一般化

所在の事実文だけを返し、所在の中身へ辿る手段を渡さない。
