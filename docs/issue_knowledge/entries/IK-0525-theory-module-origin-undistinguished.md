---
id: IK-0525
title: "理論モジュールの詳細で、式から機械合成した主張と本文の主張、推測で補った前提と導出記録の前提が同じ顔で並び、plain_math の二重バックスラッシュ・mid 記号も崩れていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/features/theory_module_layer_design.md
feature_context:
  realizing: "理論モジュールが何を要求し何を返すかを読む"
  layers: [graph_review]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [meaning]
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
    subtitle、required_claims の origin_label、assumptions の 3 出所ラベルを足し、plain_math を直した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "出所の異なる文を出所を示さずに同じ一覧に並べる"
pattern: projection-mistaken-for-source
discovery:
  perspective: [data_inspection]
  note: "教員ペルソナが合成文を本文の主張と読んだ。"
resolution:
  perspective: [representation_change]
  note: "subtitle、required_claims の origin_label、assumptions の 3 出所ラベルを足し、plain_math を直した。"
  landed_in:
    - backend/core/theory_modules/builder.py
    - frontend/public/js/admin-graph-review.js
    - backend/tests/test_theory_module_core.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

出所の違いが見えなかった。

## 発見の観点

教員ペルソナが合成文を本文の主張と読んだ。

## 解決の観点

subtitle、required_claims の origin_label、assumptions の 3 出所ラベルを足し、plain_math を直した。

## 一般化

出所の異なる文を出所を示さずに同じ一覧に並べる。
