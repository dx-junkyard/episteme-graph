---
id: IK-0561
title: "相槌（「なるほど」等）だけの発話まで構造帰属の対象にしていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat, learner_experience_b]
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
    `_is_contentless_reply` で相槌を帰属対象から外す。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "発話が届いたことを、帰属すべき問いがあることと同一視する"
pattern: completion-defined-by-proxy
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [single_point_fix]
  note: "`_is_contentless_reply` で相槌を帰属対象から外す。"
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

相槌（「なるほど」等）だけの発話まで構造帰属の対象にしていた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

`_is_contentless_reply` で相槌を帰属対象から外す。

## 一般化

発話が届いたことを、帰属すべき問いがあることと同一視する。
