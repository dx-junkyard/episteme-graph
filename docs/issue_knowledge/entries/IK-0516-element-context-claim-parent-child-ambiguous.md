---
id: IK-0516
title: "要素文脈で主張 ID を解決するとき、親と atomic 子が同じ ID で当たり、解決できない要素は 404 になっていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "教材の主張チップから文脈を開く"
  layers: [learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    _resolve_claim の親子曖昧解消と、未解決を available:false + note（ELEMENT_CONTEXT_UNRESOLVED_NOTE）で返す。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "同じ識別子に親子の複数の実体が当たるのを一意とみなし、解決できないことを失敗として返す"
pattern: id-namespace-conflated
discovery:
  perspective: [reproduction]
  note: "学生ペルソナの ⚓ が 404 になった。"
resolution:
  perspective: [single_point_fix, fail_closed]
  note: "_resolve_claim の親子曖昧解消と、未解決を available:false + note（ELEMENT_CONTEXT_UNRESOLVED_NOTE）で返す。"
  landed_in:
    - backend/core/element_context.py
    - backend/core/learner_context_common.py
    - backend/tests/test_element_context_core.py
    - backend/tests/test_element_context_api.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

要素文脈が親子曖昧で解決できず 404 だった。

## 発見の観点

学生ペルソナの ⚓ が 404 になった。

## 解決の観点

_resolve_claim の親子曖昧解消と、未解決を available:false + note（ELEMENT_CONTEXT_UNRESOLVED_NOTE）で返す。

## 一般化

同じ識別子に親子の複数の実体が当たるのを一意とみなし、解決できないことを失敗として返す。
