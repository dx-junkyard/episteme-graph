---
id: IK-0534
title: "すでに受講中のコースに受講登録しても、初回登録と同じ文言が返っていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "コースを受講登録する"
  layers: [learner_experience_b]
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
    enroll_user_in_course が新規かを返し、COURSE_ALREADY_ENROLLED_NOTICE を出す。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "冪等な操作の既存状態を、新規と同じ文言で返す"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "受講済みのペルソナが再登録した。"
resolution:
  perspective: [single_point_fix]
  note: "enroll_user_in_course が新規かを返し、COURSE_ALREADY_ENROLLED_NOTICE を出す。"
  landed_in:
    - backend/api/services.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0369_enroll_response.py
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified: []
related: []
view_of: []
history:
  - date: '2026-09-30'
    field: resolution.verification
    from: guardrail のみ・砂場再演は未確認
    to: guardrail + 砂場再演
    reason: >-
      第 15 周（persona_enactment_testing_design.md §18.7）の再演で症状の消失を頭脳メモが GONE と記録した。
---

## 課題

既受講を区別しなかった。

## 発見の観点

受講済みのペルソナが再登録した。

## 解決の観点

enroll_user_in_course が新規かを返し、COURSE_ALREADY_ENROLLED_NOTICE を出す。

## 一般化

冪等な操作の既存状態を、新規と同じ文言で返す。
