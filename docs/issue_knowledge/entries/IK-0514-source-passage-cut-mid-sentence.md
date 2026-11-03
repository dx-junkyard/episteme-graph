---
id: IK-0514
title: "出典の本文が文の途中から始まり・途中で終わっても印がなく、PDF の行末改行もそのまま出ていて、開けない出典は英語の 404 だった"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/backend/rag-chat.md
feature_context:
  realizing: "出典の本文を開いて回答の根拠を確かめる"
  layers: [rag_chat, frontend_learning_ui, learner_experience_b]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    途中始まり・終わりに「…」を付け、normalize_source_line_breaks で行末改行とハイフネーションを畳み、source-chunk 404 を日本語の事実文にした。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "切り出した断片を切り出しの印なしに全体のように見せ、失敗は内部語で返す"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "学生ペルソナが途中始まりの出典を読んで文意を取れなかった。"
resolution:
  perspective: [single_point_fix]
  note: "途中始まり・終わりに「…」を付け、normalize_source_line_breaks で行末改行とハイフネーションを畳み、source-chunk 404 を日本語の事実文にした。"
  landed_in:
    - backend/api/routes/learning.py
    - backend/core/text_excerpt.py
    - backend/tests/test_learning_citation_wave15.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

出典本文の切り出しが読めなかった。

## 発見の観点

学生ペルソナが途中始まりの出典を読んで文意を取れなかった。

## 解決の観点

途中始まり・終わりに「…」を付け、normalize_source_line_breaks で行末改行とハイフネーションを畳み、source-chunk 404 を日本語の事実文にした。

## 一般化

切り出した断片を切り出しの印なしに全体のように見せ、失敗は内部語で返す。
