---
id: IK-0552
title: "記号の lookup が表示中の論文ではなくコース全体の最も近い定義を返し、別論文の記号の定義を持ってきていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [concept_registry, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
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
    lookup の範囲を chunk の document → 表示中トピックの論文に限定し `topic_id` を受ける。別論文に同じ記号があれば FACT_SAME_SYMBOL_IN_OTHER_DOCUMENTS、この論文に無ければ FACT_SYMBOL_NOT_IN_THIS_DOCUMENT の事実文を返す。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "同じ字形の記号を、表示中の文書ではなく検索範囲全体の最も近い定義に結び付ける"
pattern: scope-widened-silently
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [explicit_contract]
  note: "lookup の範囲を chunk の document → 表示中トピックの論文に限定し `topic_id` を受ける。別論文に同じ記号があれば FACT_SAME_SYMBOL_IN_OTHER_DOCUMENTS、この論文に無ければ FACT_SYMBOL_NOT_IN_THIS_DOCUMENT の事実文を返す。"
  landed_in:
    - backend/core/symbol_lookup.py
    - backend/api/routes/learning.py
    - frontend/public/js/app.js
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: [IK-0518]
view_of: []
history: []
---

## 課題

記号の lookup が表示中の論文ではなくコース全体の最も近い定義を返し、別論文の記号の定義を持ってきていた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

lookup の範囲を chunk の document → 表示中トピックの論文に限定し `topic_id` を受ける。別論文に同じ記号があれば FACT_SAME_SYMBOL_IN_OTHER_DOCUMENTS、この論文に無ければ FACT_SYMBOL_NOT_IN_THIS_DOCUMENT の事実文を返す。

## 一般化

同じ字形の記号を、表示中の文書ではなく検索範囲全体の最も近い定義に結び付ける。
