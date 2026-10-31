---
id: IK-0557
title: "否定文の中の [出典N]（「出典N は…を述べていません」）を引用として数え、「前の回答で引用」の印を誤って付けていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    `_affirmative_citation_indices` で否定文中の出典マーカーを引用から外す。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "文中に記号が現れたことを、その記号を肯定的に使ったことと同一視する"
pattern: completion-defined-by-proxy
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [single_point_fix]
  note: "`_affirmative_citation_indices` で否定文中の出典マーカーを引用から外す。"
  landed_in:
    - backend/api/routes/learning.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: [IK-0512, IK-0545]
view_of: []
history: []
---

## 課題

否定文の中の [出典N]（「出典N は…を述べていません」）を引用として数え、「前の回答で引用」の印を誤って付けていた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

`_affirmative_citation_indices` で否定文中の出典マーカーを引用から外す。

## 一般化

文中に記号が現れたことを、その記号を肯定的に使ったことと同一視する。
