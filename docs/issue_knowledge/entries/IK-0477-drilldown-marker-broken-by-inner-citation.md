---
id: IK-0477
title: "英語の回答のドリルダウンの目印に文献番号 [98] が入れ子になり、ボタンの文字が「Ask more about what reference [98」、本文に「 found about μ]」が残った"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "回答の末尾の「詳しく聞く」ボタンを回答の文言から正しく作る"
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
    処理軸: _DRILLDOWN_EN_RE / _DRILLDOWN_RE の中身は [^\]]{2,80} で、内側の ] で目印が終わっていた（input_handling）。
generalization:
  level: general
  general_form: "区切り記号で囲む目印の中に同じ区切り記号が入れ子になると、最初の閉じ記号で切れる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [symptom_report]
  note: "学習者（st-04）の感想「the second button is cut off: Ask more about what reference [98.」。"
resolution:
  perspective: [single_point_fix]
  note: >-
    目印の中身に1段の角括弧（24字まで）を許し、ボタンの文字からは [出典N] を落とし、文献番号 [98] は角括弧だけ外して番号を残す。
  landed_in:
    - backend/core/learning_support_agent.py
    - backend/tests/test_ik0466_0478_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演"
related: [IK-0425, IK-0478]
view_of: []
history: []
---

## 課題

英語の回答のドリルダウンの目印に文献番号 [98] が入れ子になり、ボタンの文字が「Ask more about what reference [98」、本文に「 found about μ]」が残った

## 発見の観点

学習者（st-04）の感想「the second button is cut off: Ask more about what reference [98.」。

## 解決の観点

目印の中身に1段の角括弧（24字まで）を許し、ボタンの文字からは [出典N] を落とし、文献番号 [98] は角括弧だけ外して番号を残す。

## 一般化

区切り記号で囲む目印の中に同じ区切り記号が入れ子になると、最初の閉じ記号で切れる。
