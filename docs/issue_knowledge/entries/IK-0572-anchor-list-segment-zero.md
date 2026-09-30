---
id: IK-0572
title: "教材の ⚓ 一覧が 25 件を超えると全部「区画0」になり、どの区画の要素か分からない"
status: open
recorded_at: 2026-09-30
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [learner_experience_b, frontend_learning_ui]
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
    原因（投影側か配信側か）の切り分けが未了。 原因の性質で座標を置いた（症状の場所ではなく）。 未解決のため座標は暫定。
generalization:
  level: general
  general_form: "要素の所在を区画に写す段で、写せないものを先頭区画へ落とす"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [pending]
  note: "未解決。第 16 波では是正していない。"
  landed_in: []
related: []
view_of: []
history: []
---

## 課題

教材の ⚓ 一覧が 25 件を超えると全部「区画0」になり、どの区画の要素か分からない。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

未解決。原因（投影側か配信側か）の切り分けが未了。

## 一般化

要素の所在を区画に写す段で、写せないものを先頭区画へ落とす。
