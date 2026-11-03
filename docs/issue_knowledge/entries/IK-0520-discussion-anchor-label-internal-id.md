---
id: IK-0520
title: "「わたしの地図」で discuss 由来の痕跡のアンカー名が疑似トピック ID（_discussion）のまま出ていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "わたしの地図で自分の問いの痕跡を見る"
  layers: [personal_network, discuss]
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
    derive で _discussion を「論文との議論」に変換。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "予約された内部 ID を表示名へ変換する経路が、一部の導出経路で抜ける"
pattern: wording-mismatch
discovery:
  perspective: [data_inspection]
  note: "投影に _discussion が出た。"
resolution:
  perspective: [single_point_fix]
  note: "derive で _discussion を「論文との議論」に変換。"
  landed_in:
    - backend/core/personal_graph/derive.py
    - backend/tests/test_personal_graph_derive.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

内部 ID がラベルに出た。

## 発見の観点

投影に _discussion が出た。

## 解決の観点

derive で _discussion を「論文との議論」に変換。

## 一般化

予約された内部 ID を表示名へ変換する経路が、一部の導出経路で抜ける。
