---
id: IK-0560
title: "前提ゲート後の「まだ」「わからん」「むずい」を肯定の返事として扱い、前提の説明にトピックの教材本文を渡していなかった"
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
    口語の否定を否定の返事に含め、前提トピックの教材本文を説明の入力に渡す。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "返事の語彙を狭い否定語の列挙でだけ判定し、口語の否定を取りこぼす"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [single_point_fix]
  note: "口語の否定を否定の返事に含め、前提トピックの教材本文を説明の入力に渡す。"
  landed_in:
    - backend/api/routes/learning.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: [IK-0509]
view_of: []
history: []
---

## 課題

前提ゲート後の「まだ」「わからん」「むずい」を肯定の返事として扱い、前提の説明にトピックの教材本文を渡していなかった。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

口語の否定を否定の返事に含め、前提トピックの教材本文を説明の入力に渡す。

## 一般化

返事の語彙を狭い否定語の列挙でだけ判定し、口語の否定を取りこぼす。
