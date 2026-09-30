---
id: IK-0523
title: "同じ構造のモジュールを持つ論文が無いとき、related が空を返すだけで事実文が無かった"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/features/theory_module_layer_design.md
feature_context:
  realizing: "理論モジュールと同じ構造の他論文を見る"
  layers: [graph_review]
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
    FACT_RELATED_NONE（閉世界の事実文）を足した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "候補ゼロを空の一覧で返し、ゼロであることを言わない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "教員ペルソナが空の区画を不具合と読んだ。"
resolution:
  perspective: [single_point_fix]
  note: "FACT_RELATED_NONE（閉世界の事実文）を足した。"
  landed_in:
    - backend/core/theory_modules/related.py
    - backend/tests/test_theory_module_identity.py
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

related の空に事実文が無かった。

## 発見の観点

教員ペルソナが空の区画を不具合と読んだ。

## 解決の観点

FACT_RELATED_NONE（閉世界の事実文）を足した。

## 一般化

候補ゼロを空の一覧で返し、ゼロであることを言わない。
