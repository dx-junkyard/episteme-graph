---
id: IK-0574
title: "前提ゲートの逆質問が英語で話す学習者にも日本語で出る"
status: open
recorded_at: 2026-09-30
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
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
    ゲート文は固定の日本語。 原因の性質で座標を置いた（症状の場所ではなく）。 未解決のため座標は暫定。
generalization:
  level: general
  general_form: "発話の言語を固定文の生成まで伝えない"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [pending]
  note: "未解決。第 16 波では是正していない。"
  landed_in: []
related: [IK-0509]
view_of: []
history: []
---

## 課題

前提ゲートの逆質問が英語で話す学習者にも日本語で出る。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

未解決。ゲート文は固定の日本語。

## 一般化

発話の言語を固定文の生成まで伝えない。
