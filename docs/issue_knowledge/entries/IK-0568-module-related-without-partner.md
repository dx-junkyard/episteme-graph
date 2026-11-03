---
id: IK-0568
title: "理論モジュール related が相手のいない外枠まで並べ、確認済みの範囲も示していなかった"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [graph_review]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    相手のいる外枠だけを返し、`checked_module_keys` で確認済みの範囲を別に返す。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "照合の結果が無い項目を結果のある項目と同じ形で返す"
pattern: failure-reported-as-success
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [explicit_contract]
  note: "相手のいる外枠だけを返し、`checked_module_keys` で確認済みの範囲を別に返す。"
  landed_in:
    - backend/core/theory_modules/related.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: [IK-0523]
view_of: []
history: []
---

## 課題

理論モジュール related が相手のいない外枠まで並べ、確認済みの範囲も示していなかった。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

相手のいる外枠だけを返し、`checked_module_keys` で確認済みの範囲を別に返す。

## 一般化

照合の結果が無い項目を結果のある項目と同じ形で返す。
