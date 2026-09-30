---
id: IK-0576
title: "訂正が次の往復で再訂正される（抜粋の違いで結論が変わる）"
status: open
recorded_at: 2026-09-30
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat]
classification:
  axes:
    processing: [unknown]
    structure: [none]
    connection: [version]
    governance: [none]
  axis_confidence:
    processing: low
    structure: low
    connection: low
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 検索揺れが原因と見ているが未確定。 原因の性質で座標を置いた（症状の場所ではなく）。 未解決のため座標は暫定。
generalization:
  level: general
  general_form: "前のターンの結論を持ち越さず、毎回の検索結果だけで答え直す"
pattern: unit-of-work-undefined
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [pending]
  note: "未解決。第 16 波では是正していない。"
  landed_in: []
related: [IK-0545]
view_of: []
history: []
---

## 課題

訂正が次の往復で再訂正される（抜粋の違いで結論が変わる）。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

未解決。検索揺れが原因と見ているが未確定。

## 一般化

原因は仮説（未確定）。
前のターンの結論を持ち越さず、毎回の検索結果だけで答え直す。
