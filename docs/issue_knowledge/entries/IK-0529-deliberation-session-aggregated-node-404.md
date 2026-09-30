---
id: IK-0529
title: "主グラフの集約ノードでノード対話を開くと、理由のない 404 になっていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "グラフレビューのノードから要素対話を始める"
  layers: [deliberation_w, graph_review]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
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
    create_session で集約ノードを FACT_AGGREGATED_MAIN_NODE の事実文で拒否。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "対象になれない種類の要素にも入口を開け、拒否を理由なしで返す"
pattern: entry-scope-mismatch
discovery:
  perspective: [reproduction]
  note: "教員ペルソナのノード対話が 404。"
resolution:
  perspective: [single_point_fix]
  note: "create_session で集約ノードを FACT_AGGREGATED_MAIN_NODE の事実文で拒否。"
  landed_in:
    - backend/api/routes/deliberation.py
    - backend/tests/test_deliberation_api.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

集約ノードで 404。

## 発見の観点

教員ペルソナのノード対話が 404。

## 解決の観点

create_session で集約ノードを FACT_AGGREGATED_MAIN_NODE の事実文で拒否。

## 一般化

対象になれない種類の要素にも入口を開け、拒否を理由なしで返す。
