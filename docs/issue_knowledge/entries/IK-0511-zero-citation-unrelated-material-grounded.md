---
id: IK-0511
title: "回答本文が出典を 1 つも引用せず表示中の教材も問いと無関係なのに、回答の出所を「教材に基づく」と表示していた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/backend/rag-chat.md
feature_context:
  realizing: "学習チャットの回答が何に基づくかを学習者に示す"
  layers: [rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
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
    引用ゼロかつ表示中教材が問いに無関係なら model_generated にする（第 14 周（uxsim/runs/c-astro-structure-30）の裁定）。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "出所の判定を「資料を持っている」ことで行い、応答が実際に資料を使ったかを見ない"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection, symptom_report]
  note: "審判 C が引用ゼロの回答に course_material が付く往復を挙げた。"
resolution:
  perspective: [single_point_fix]
  note: "引用ゼロかつ表示中教材が問いに無関係なら model_generated にする（第 14 周の裁定）。"
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0492_0496_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

引用ゼロの回答が「教材に基づく」と表示されていた。

## 発見の観点

審判 C が引用ゼロの回答に course_material が付く往復を挙げた。

## 解決の観点

引用ゼロかつ表示中教材が問いに無関係なら model_generated にする（第 14 周の裁定）。

## 一般化

出所の判定を「資料を持っている」ことで行い、応答が実際に資料を使ったかを見ない。
