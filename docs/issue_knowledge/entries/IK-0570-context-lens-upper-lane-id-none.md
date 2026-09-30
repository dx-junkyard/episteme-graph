---
id: IK-0570
title: "部品の文脈の上位レーン（中心命題・支持構造）の項目が id=None で上へ辿れない"
status: open
recorded_at: 2026-09-30
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [deliberation_w, learner_experience_b]
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
    第 15 周の trace 場面で、上位 6 件が同列の「（中心命題）を支持する」で id=None だった。W層 context_lens の上位レーン導出に由来。 原因の性質で座標を置いた（症状の場所ではなく）。 未解決のため座標は暫定。
generalization:
  level: general
  general_form: "文脈の項目を名前だけで作り、辿り先の参照を持たせない"
pattern: available-but-unwired
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

部品の文脈の上位レーン（中心命題・支持構造）の項目が id=None で上へ辿れない。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

未解決。第 15 周の trace 場面で、上位 6 件が同列の「（中心命題）を支持する」で id=None だった。W層 context_lens の上位レーン導出に由来。

## 一般化

文脈の項目を名前だけで作り、辿り先の参照を持たせない。
