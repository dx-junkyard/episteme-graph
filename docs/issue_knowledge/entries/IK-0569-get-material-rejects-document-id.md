---
id: IK-0569
title: "教材詳細の取得が material_id しか受けず、documents.id では英語の 404 を返していた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [auth_visibility, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
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
    `get_material` が document_id でも解決し、404 は日本語の事実文にした。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "同じ対象を指す 2 つの ID 体系の片方だけを入口で受ける"
pattern: id-namespace-conflated
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [single_point_fix]
  note: "`get_material` が document_id でも解決し、404 は日本語の事実文にした。"
  landed_in:
    - backend/api/routes/admin.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: [IK-0550]
view_of: []
history: []
---

## 課題

教材詳細の取得が material_id しか受けず、documents.id では英語の 404 を返していた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

`get_material` が document_id でも解決し、404 は日本語の事実文にした。

## 一般化

同じ対象を指す 2 つの ID 体系の片方だけを入口で受ける。
