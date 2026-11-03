---
id: IK-0539
title: "PDF から壊れて復元された式（置換文字入り）を式として描いていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "教材の式を読む"
  layers: [lecture_player, learner_experience_b]
classification:
  axes:
    processing: [input_handling]
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
    is_broken_reconstructed_latex で判定し latex_withheld（事実文だけ残す）。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "壊れた外部由来の値を検査せずに描画器へ渡す"
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [data_inspection]
  note: "投影に置換文字入りの式。"
resolution:
  perspective: [single_point_fix]
  note: "is_broken_reconstructed_latex で判定し latex_withheld（事実文だけ残す）。"
  landed_in:
    - backend/core/lecture.py
    - backend/tests/test_triage14_learning_display.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

壊れた式を描いた。

## 発見の観点

投影に置換文字入りの式。

## 解決の観点

is_broken_reconstructed_latex で判定し latex_withheld（事実文だけ残す）。

## 一般化

壊れた外部由来の値を検査せずに描画器へ渡す。
