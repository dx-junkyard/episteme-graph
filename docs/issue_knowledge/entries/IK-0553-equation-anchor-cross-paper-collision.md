---
id: IK-0553
title: "式 ⚓ の equation_id（eq_5 等）が論文をまたいで衝突し、別論文の式の文脈を開いていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [learner_experience_b, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    `_resolve_equation` を表示中トピックの論文で優先解決し、なお複数の論文に当たるときは未解決（available:false）にする。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "文書内でしか一意でない ID を、文書を指定せずに複数文書の範囲で解決する"
pattern: id-namespace-conflated
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [single_point_fix]
  note: "`_resolve_equation` を表示中トピックの論文で優先解決し、なお複数の論文に当たるときは未解決（available:false）にする。"
  landed_in:
    - backend/core/element_context.py
    - backend/api/routes/learning.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: [IK-0550]
view_of: []
history: []
---

## 課題

式 ⚓ の equation_id（eq_5 等）が論文をまたいで衝突し、別論文の式の文脈を開いていた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

`_resolve_equation` を表示中トピックの論文で優先解決し、なお複数の論文に当たるときは未解決（available:false）にする。

## 一般化

文書内でしか一意でない ID を、文書を指定せずに複数文書の範囲で解決する。
