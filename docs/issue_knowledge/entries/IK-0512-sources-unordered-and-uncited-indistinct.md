---
id: IK-0512
title: "回答の出典一覧が番号順でなく、本文が引用していない出典も引用したものと同じ顔で並んでいた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/backend/rag-chat.md
feature_context:
  realizing: "回答の出典番号から該当する資料を開く"
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    sources を番号順に並べ、SourceTierItem に cited（本文が引用したか）を足して画面で区別する。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "検索で採用した候補の一覧を、応答が実際に参照した一覧として見せる"
pattern: projection-mistaken-for-source
discovery:
  perspective: [reproduction]
  note: "学生ペルソナが「出典3」を開こうとして一覧の順と番号が合わず迷った。同番号が別チャンクに見えた件は実物照合で LLM の誤帰属と判明（番号対応自体は壊れていない）。"
resolution:
  perspective: [representation_change]
  note: "sources を番号順に並べ、SourceTierItem に cited（本文が引用したか）を足して画面で区別する。"
  landed_in:
    - backend/api/schemas.py
    - backend/api/routes/learning.py
    - frontend/public/js/app.js
    - backend/tests/test_learning_citation_wave15.py
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified: []
related: []
view_of: []
history:
  - date: '2026-09-30'
    field: resolution.verification
    from: guardrail のみ・砂場再演は未確認
    to: guardrail + 砂場再演
    reason: >-
      第 15 周（persona_enactment_testing_design.md §18.7）の再演で症状の消失を頭脳メモが GONE と記録した。
---

## 課題

出典一覧の順が番号順でなく、引用されていない出典の区別もなかった。

## 発見の観点

学生ペルソナが「出典3」を開こうとして一覧の順と番号が合わず迷った。同番号が別チャンクに見えた件は実物照合で LLM の誤帰属と判明（番号対応自体は壊れていない）。

## 解決の観点

sources を番号順に並べ、SourceTierItem に cited（本文が引用したか）を足して画面で区別する。

## 一般化

検索で採用した候補の一覧を、応答が実際に参照した一覧として見せる。
