---
id: IK-0425
title: "英語で答えた回答の末尾のドリルダウンが `[Ask more about …]` の形で本文にそのまま残り、深掘りの選択肢にならなかった（目印の解析は日本語の `[〇〇について詳しく聞く]` だけを認識していた）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習チャットの回答末尾の深掘り候補を、回答の言語によらず選択肢として出す
  layers: [rag_chat]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [contract]
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
    接続軸: 生成側は受講者の言語で答え（英語の回答では目印も英語に訳される）、解析側
    （core/learning_support_agent.py _DRILLDOWN_RE）は日本語の目印だけを受ける契約で、両者が両立していなかった（contract）。
    処理軸: 英語の目印を入力として受理しなかった（input_handling。medium: 契約のずれの症状とも読める）。構造軸・統制軸は
    none。確認: 第 9 周の英語ペルソナの回答本文に `[Ask more about …]` の行が残っていた。
generalization:
  level: repo_pattern
  general_form: 生成物の言語が利用者に合わせて変わるのに、生成物に埋める機械用の目印を一つの言語の形でしか解析しない
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction]
  note: 第 9 周の英語ペルソナの回答本文を読んだ。
resolution:
  perspective: [single_point_fix, explicit_contract]
  note: >-
    解析に英語の目印 `[Ask more about X]` / `[Ask about X]` / `[Tell me more about X]`（大文字小文字を区別しない）を足し、
    日本語形と同じ drilldown の next_actions にして本文から除く。チューターのプロンプトに、英語で答えるときの目印の形を
    一文で明示した。
  landed_in:
    - backend/core/learning_support_agent.py
    - backend/api/routes/learning.py
    - backend/tests/test_ik0422_prerequisite_gate_once.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 読み上げ（core/tts.py の目印除去は日本語形だけのまま）
      - 上に挙げた以外の英語の言い回しの目印
related: []
view_of: []
history: []
---

## 課題

英語の回答のドリルダウン目印が本文に残る。

## 発見の観点

英語ペルソナの回答本文を読んだ。

## 解決の観点

英語の目印も解析し、プロンプトで形を明示する。

## 一般化

生成物の言語が変わるのに機械用の目印を一つの言語でしか解析しない型。
