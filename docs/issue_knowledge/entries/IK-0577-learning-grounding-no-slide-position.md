---
id: IK-0577
title: "学習チャットの grounding に表示中のスライド位置・復元式の印が載らない"
status: open
recorded_at: 2026-09-30
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat, screen_adapter_sa]
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
    SA層 Phase 4-c（topic / visible）は保留のまま。 原因の性質で座標を置いた（症状の場所ではなく）。 未解決のため座標は暫定。
generalization:
  level: general
  general_form: "画面の状態を持っているのに AI の入力へ渡さない"
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

学習チャットの grounding に表示中のスライド位置・復元式の印が載らない。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

未解決。SA層 Phase 4-c（topic / visible）は保留のまま。

## 一般化

画面の状態を持っているのに AI の入力へ渡さない。
