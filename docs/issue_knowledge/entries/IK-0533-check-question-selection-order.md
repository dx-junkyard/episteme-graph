---
id: IK-0533
title: "確認問題の選択で、要求された問いとの照合順が誤り、別の問いで採点していた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "トピックの確認問題に答える"
  layers: [learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    _select_check_question の照合順を直した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "複数の照合キーの優先順を誤り、別の対象を選ぶ"
pattern: same-name-different-referents
discovery:
  perspective: [reproduction]
  note: "学生ペルソナの答えが別の問いで評価された。"
resolution:
  perspective: [single_point_fix]
  note: "_select_check_question の照合順を直した。"
  landed_in:
    - backend/api/routes/learning.py
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

別の問いが選ばれた。

## 発見の観点

学生ペルソナの答えが別の問いで評価された。

## 解決の観点

_select_check_question の照合順を直した。

## 一般化

複数の照合キーの優先順を誤り、別の対象を選ぶ。
