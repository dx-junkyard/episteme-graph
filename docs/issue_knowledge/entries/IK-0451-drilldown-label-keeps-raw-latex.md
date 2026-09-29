---
id: IK-0451
title: "`[Ask more about $\\mu$]` から作ったドリルダウンのボタンの文字に、生の `$…$` が残った"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: ドリルダウンのボタンが読める文字になる
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
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
    処理軸: extract_inline_actions は目印の中身をそのままボタンの文字と送信文にしていた。回答本文では数式の区切りとして描かれる `$…$` / `\\(…\\)`
    が、ボタンではプレーンテキストとして出た（input_handling）。
generalization:
  level: instance
  general_form: 表示の文脈が変わる箇所で、元の文脈の記法を外さずに渡す
pattern: contract-changed-one-side
discovery:
  perspective: [data_inspection]
  note: 第 10 周（c-astro-verify-wave456）の transcript の往復ごとの sources と mailbox の実プロンプト（req-*.json）を並べた。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    _strip_math_delimiters で `$$…$$` / `$…$` / `\\(…\\)` / `\\[…\\]` の区切りを外し中身だけを残してからボタンの文字と送信文にする（日本語・英語の目印とも）。
  landed_in:
    - backend/core/learning_support_agent.py
    - backend/tests/test_ik0444_0451_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 区切りの中のバックスラッシュ記法（\\mu 等）はそのまま残る
related: [IK-0425]
view_of: []
history: []
---

## 課題

ボタンの文字に生の数式区切りが残る。

## 発見の観点

英語ペルソナの第 10 周の報告を読んだ。

## 解決の観点

ボタンにする前に区切りを外す。

## 一般化

文脈の違う表示先へ記法を外さずに渡す型。
