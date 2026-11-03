---
id: IK-0558
title: "「さっきの出典」への問い返しで、直前に引用したチャンクを検索し直して別のチャンクを読んでいた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat]
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
    直前の引用チャンクを id 指定で必ず読み出す（検索 0 回）。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "直前のターンが持っていた参照を、次のターンで検索に置き換えて失う"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [carry_through]
  note: "直前の引用チャンクを id 指定で必ず読み出す（検索 0 回）。"
  landed_in:
    - backend/api/routes/learning.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

「さっきの出典」への問い返しで、直前に引用したチャンクを検索し直して別のチャンクを読んでいた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

直前の引用チャンクを id 指定で必ず読み出す（検索 0 回）。

## 一般化

直前のターンが持っていた参照を、次のターンで検索に置き換えて失う。
