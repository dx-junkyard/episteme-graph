---
id: IK-0576
title: "訂正が次の往復で再訂正される（抜粋の違いで結論が変わる）"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
  - docs/features/dialogue_response_shape_design.md
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
  perspective: [carry_through]
  note: "直前の assistant 往復の訂正文（逐語・200 字まで）を [前の往復での訂正] として次の往復の system に持ち越し、撤回するなら撤回と明示させる（検索結果の違いだけで結論を変えない）。casual / elicit では持ち越さない。"
  landed_in:
    - backend/core/dialogue_shape.py
    - backend/api/routes/learning.py
    - backend/tests/test_dialogue_shape_core.py
    - backend/tests/test_dialogue_shape_route.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（ペルソナ通し受講の次の周）
      - 実 LLM 出力での問いの切り分けの取りこぼし（箇条書き末尾の問いなど）
related: [IK-0545]
view_of: []
history: []
---

## 課題

訂正が次の往復で再訂正される（抜粋の違いで結論が変わる）。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

previous_correction_fact で前の往復の訂正を持ち越す（応答の骨格 §6）。

## 一般化

原因は仮説（未確定）。
前のターンの結論を持ち越さず、毎回の検索結果だけで答え直す。
