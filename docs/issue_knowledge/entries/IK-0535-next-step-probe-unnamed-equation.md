---
id: IK-0535
title: "「次の一手」の出題が、式を名指しできない導出 step からも問いを作り、何の式か分からない問いになっていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "理解サイクルで導出の次の一手を予想する"
  layers: [reconstruction_r, understanding_cycle]
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
    式の表示名を作れない step は出題しない。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "必要な名前を欠く素材からも問いを組んで空欄を出す"
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [data_inspection]
  note: "投影の問いに式名が無かった。"
resolution:
  perspective: [single_point_fix]
  note: "式の表示名を作れない step は出題しない。"
  landed_in:
    - backend/core/reconstruction/derivation_source.py
    - backend/tests/test_understanding_cycle_regime.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

名指せない問い。

## 発見の観点

投影の問いに式名が無かった。

## 解決の観点

式の表示名を作れない step は出題しない。

## 一般化

必要な名前を欠く素材からも問いを組んで空欄を出す。
