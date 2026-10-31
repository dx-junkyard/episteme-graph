---
id: IK-0521
title: "「いまここの周り」の範囲表示で、グラフ未構築の論文があることを黙っていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "いまここの周りで自分の位置の近傍を見る"
  layers: [personal_network]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    「グラフ未構築の論文」の事実文を最大 3 件足した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "範囲に含まれるが描けない対象を、描けないと言わずに省く"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "範囲表示に論文が欠けていた。"
resolution:
  perspective: [single_point_fix]
  note: "「グラフ未構築の論文」の事実文を最大 3 件足した。"
  landed_in:
    - backend/core/personal_graph/nearby.py
    - backend/tests/test_personal_map_nearby.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

描けない論文が黙って省かれた。

## 発見の観点

範囲表示に論文が欠けていた。

## 解決の観点

「グラフ未構築の論文」の事実文を最大 3 件足した。

## 一般化

範囲に含まれるが描けない対象を、描けないと言わずに省く。
