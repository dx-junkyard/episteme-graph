---
id: IK-0555
title: "付録「この節で使う数式」に本文が触れない 1 文字記号＝数値の式（N=15 等）が並び、英語の読み上げ文まで付いていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [course_builder, learner_experience_b]
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
    本文が触れない 1 文字記号＝数値の式を付録から除く。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "本文との関係を見ずに、束ねた要素に付く式をすべて付録へ流す"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [single_point_fix]
  note: "本文が触れない 1 文字記号＝数値の式を付録から除く。"
  landed_in:
    - backend/core/course_content_builder.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

付録「この節で使う数式」に本文が触れない 1 文字記号＝数値の式（N=15 等）が並び、英語の読み上げ文まで付いていた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

本文が触れない 1 文字記号＝数値の式を付録から除く。

## 一般化

本文との関係を見ずに、束ねた要素に付く式をすべて付録へ流す。
