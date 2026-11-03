---
id: IK-0510
title: "予想（elicit）の往復に採用チャンクの本文を渡していたため、AI が学習者に予想させる前に答えを知った状態で問いを立てていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/backend/rag-chat.md
feature_context:
  realizing: "理解サイクルの予想段で、答えを見せずに学習者の予想を引き出す"
  layers: [understanding_cycle, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    cycle_mode=elicit では採用チャンク本文を LLM 入力に載せない。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "答えを伏せる段の入力に、伏せるべき答えそのものが通常経路の文脈として混入する"
pattern: scope-widened-silently
discovery:
  perspective: [data_inspection]
  note: "mailbox の製品 prompt（elicit の往復）に採用チャンク本文が載っていた。"
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: "cycle_mode=elicit では採用チャンク本文を LLM 入力に載せない。"
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_triage14_elicit_no_answer_context.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

予想を求める往復の入力に、出典本文（＝答え）が入っていた。

## 発見の観点

mailbox の製品 prompt（elicit の往復）に採用チャンク本文が載っていた。

## 解決の観点

cycle_mode=elicit では採用チャンク本文を LLM 入力に載せない。

## 一般化

答えを伏せる段の入力に、伏せるべき答えそのものが通常経路の文脈として混入する。
