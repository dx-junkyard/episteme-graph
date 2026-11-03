---
id: IK-0517
title: "学習者向け要素文脈の ITEM v2 で、agent ID の遮断が component レーンだけで、主張・式のレーンや導出項目の英語生成文・件数が学習者に出ていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "学習者に要素の上位・下位の文脈を見せる"
  layers: [learner_experience_b, shared_infra]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [review]
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
    agent ID 遮断を claim・equation に拡張し、derivation 項目は一般ラベルに（件数を出さない）。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "内部表示を遮る規則を最初のレーンにだけ掛け、同じ射影の他のレーンに広げない"
pattern: guardrail-does-not-cover-new-path
discovery:
  perspective: [data_inspection]
  note: "投影に claim_ / eq_ 形の ID と英語の操作名が出た。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "agent ID 遮断を claim・equation に拡張し、derivation 項目は一般ラベルに（件数を出さない）。"
  landed_in:
    - backend/core/learner_context_common.py
    - backend/tests/test_learner_context_common_guardrails.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

内部 ID・英語生成文が学習者に見えた。

## 発見の観点

投影に claim_ / eq_ 形の ID と英語の操作名が出た。

## 解決の観点

agent ID 遮断を claim・equation に拡張し、derivation 項目は一般ラベルに（件数を出さない）。

## 一般化

内部表示を遮る規則を最初のレーンにだけ掛け、同じ射影の他のレーンに広げない。
