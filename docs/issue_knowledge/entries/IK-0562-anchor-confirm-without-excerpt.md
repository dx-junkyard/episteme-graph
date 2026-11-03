---
id: IK-0562
title: "帰属の確認プロンプト（anchor_confirm）が問いの抜粋も問いかけ文も持たず、どの発話への確認か分からなかった"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat, frontend_learning_ui]
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
    anchor_confirm に `excerpt`（40 字）と `prompt`（ANCHOR_CONFIRM_PROMPT）を足した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "確認を求める UI 契約に、何を確認するのかの手掛かりを載せない"
pattern: last-mile-missing
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [explicit_contract]
  note: "anchor_confirm に `excerpt`（40 字）と `prompt`（ANCHOR_CONFIRM_PROMPT）を足した。"
  landed_in:
    - backend/api/routes/learning.py
    - backend/api/schemas.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

帰属の確認プロンプト（anchor_confirm）が問いの抜粋も問いかけ文も持たず、どの発話への確認か分からなかった。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

anchor_confirm に `excerpt`（40 字）と `prompt`（ANCHOR_CONFIRM_PROMPT）を足した。

## 一般化

確認を求める UI 契約に、何を確認するのかの手掛かりを載せない。
