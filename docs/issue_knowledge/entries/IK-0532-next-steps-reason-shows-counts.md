---
id: IK-0532
title: "「次にやること」の理由文に件数が入っていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "教員が次の一歩を読む"
  layers: [guidance_g]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    件数を除き、ガードレールに加えた。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "事実文に数値を混ぜる"
pattern: wording-mismatch
discovery:
  perspective: [data_inspection]
  note: "投影の理由文に数値。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "件数を除き、ガードレールに加えた。"
  landed_in:
    - backend/core/admin_assistant/next_steps.py
    - backend/tests/test_next_steps_guardrails.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

理由文に件数（G6 違反）。

## 発見の観点

投影の理由文に数値。

## 解決の観点

件数を除き、ガードレールに加えた。

## 一般化

事実文に数値を混ぜる。
