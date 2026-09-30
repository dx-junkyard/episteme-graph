---
id: IK-0571
title: "A層由来の英語の生成文（⚓ 本文・summary・teaching_takeaway・図キャプション）が日本語の学習画面にそのまま出る"
status: open
recorded_at: 2026-09-30
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [pipeline_a, learner_experience_b]
classification:
  axes:
    processing: [unknown]
    structure: [representation]
    connection: [none]
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
    仮説: 構造課題。表示側で隠すか A層で日本語を生成するか、翻訳の置き場が未定。 原因の性質で座標を置いた（症状の場所ではなく）。 未解決のため座標は暫定。
generalization:
  level: general
  general_form: "生成物の言語を表示先の言語と照合する段が無い"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [pending]
  note: "未解決。第 16 波では是正していない。"
  landed_in: []
related: [IK-0546, IK-0540]
view_of: []
history: []
---

## 課題

A層由来の英語の生成文（⚓ 本文・summary・teaching_takeaway・図キャプション）が日本語の学習画面にそのまま出る。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

未解決。構造課題。表示側で隠すか A層で日本語を生成するか、翻訳の置き場が未定。

## 一般化

原因は仮説（未確定）。
生成物の言語を表示先の言語と照合する段が無い。
