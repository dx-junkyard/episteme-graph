---
id: IK-0515
title: "学ぶ単位（section_block）で組んだトピックには部品（component）が束ねられず、教材の ⚓ が出なかった"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/features/learning_units_design.md
feature_context:
  realizing: "コースのトピックから論文の部品の根拠カードを開く"
  layers: [learning_units, course_builder]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    _components_linked_to_claims で、トピックの主張に結ぶ同一論文の部品を上限 4 まで束ねる。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "新しい単位で束ねたとき、旧い単位の経路が持っていた付随物の結線を引き継がない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "投影の ⚓ が 0 件のトピックが多かった。"
resolution:
  perspective: [carry_through]
  note: "_components_linked_to_claims で、トピックの主張に結ぶ同一論文の部品を上限 4 まで束ねる。"
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_triage14_section_unit_components.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

section_block のトピックに部品が無かった。

## 発見の観点

投影の ⚓ が 0 件のトピックが多かった。

## 解決の観点

_components_linked_to_claims で、トピックの主張に結ぶ同一論文の部品を上限 4 まで束ねる。

## 一般化

新しい単位で束ねたとき、旧い単位の経路が持っていた付随物の結線を引き継がない。
