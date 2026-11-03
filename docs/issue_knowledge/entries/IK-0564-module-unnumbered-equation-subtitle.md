---
id: IK-0564
title: "理論モジュールの番号なしの式・内側モジュールが内部 ID か空の副題で表示されていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [graph_review]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    「番号なしの式（左辺 X）」の表示と内側モジュールの subtitle を足した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "印字番号を持たない要素の表示名を用意せず内部 ID に落とす"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [single_point_fix]
  note: "「番号なしの式（左辺 X）」の表示と内側モジュールの subtitle を足した。"
  landed_in:
    - backend/core/theory_modules/schema.py
    - backend/core/theory_modules/builder.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: [IK-0524, IK-0525]
view_of: []
history: []
---

## 課題

理論モジュールの番号なしの式・内側モジュールが内部 ID か空の副題で表示されていた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

「番号なしの式（左辺 X）」の表示と内側モジュールの subtitle を足した。

## 一般化

印字番号を持たない要素の表示名を用意せず内部 ID に落とす。
