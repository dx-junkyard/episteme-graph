---
id: IK-0528
title: "グラフ全体対話の grounding で、ノードの表示ラベルと説明が同じ文を二重に並べていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "グラフ全体を AI と確かめる"
  layers: [graph_review]
classification:
  axes:
    processing: [logic]
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
    説明があるときは短い label を使う。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "要約を含む表示名と元の説明を併記して同じ文が二度出る"
pattern: duplicate-canonical-sources
discovery:
  perspective: [data_inspection]
  note: "mailbox seq89/91 の grounding に同文が並んだ。"
resolution:
  perspective: [single_point_fix]
  note: "説明があるときは短い label を使う。"
  landed_in:
    - backend/core/deliberation/graph_dialogue.py
    - backend/tests/test_graph_review_core.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

grounding が冗長だった。

## 発見の観点

mailbox seq89/91 の grounding に同文が並んだ。

## 解決の観点

説明があるときは短い label を使う。

## 一般化

要約を含む表示名と元の説明を併記して同じ文が二度出る。
