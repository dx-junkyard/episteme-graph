---
id: IK-0401
title: "違和感の候補抽出（tension）のプロンプトは paraphrase を「学習者の言語で」と指示するのに、validator は日本語の推量語尾だけを受け付け、英語の学習者の候補は必ず修復失敗になっていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者の対話から「理解した上での引っかかり」の候補を非同期に抽出し、本人に確かめてもらう（TensionMiningAgent）
  layers: [learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [contract]
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
    接続軸 contract: 同じ出力について、プロンプト規則 4 は「学習者の言語で・推量の文体で」と言い、validator の
    _paraphrase_errors は「かもしれません / ように見えます / のかも」の末尾一致を必須にしていた（片側だけが言語に依存）。
    英語の学習者には英語で書かせておいて、その英語を不合格にする。処理軸 logic: 英語の推量は文末ではなく助動詞・副詞に出るので、
    末尾一致では判定できない。確認: 第 8 周で英語の学生の候補が 2 回とも repair_failed（unclassified）で保存された。
generalization:
  level: repo_pattern
  general_form: 生成側の指示が入力に応じて出力の形を変えるのに、検査側が一方の形だけを前提にしていて、指示に従った出力を落とす
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction, invariant_audit]
  note: 英語の学生の違和感候補が毎回 unclassified になるのを、修復ループのエラー文から validator の規則まで辿った。
resolution:
  perspective: [explicit_contract]
  note: >-
    学習者発話の言語を判定（かな・漢字に対して英字が 4 倍を超えれば英語。日本語の発話に術語・LaTeX が混じっても日本語のまま）し、
    英語の学習者には may / might / could / perhaps / possibly / seem(s) / appear(s) の語境界付き含有を推量形として受ける
    （日本語の推量末尾も受ける）。断定形の検査（P2）は両言語で常に行い、英語の断定形（you feel / you think / definitely /
    must be など）を足した。プロンプト規則 4 にも英語の推量形と断定形の例を書いた。
  landed_in:
    - backend/core/tension/validator.py
    - backend/core/tension/prompt.py
    - backend/tests/test_wave6_tension_worker_inputs.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での英語の学生による再演（候補が repair_failed にならずに保存されるか）
      - 日英が半々に混じる学習者の言語判定（閾値は実測で決めていない）
related: [IK-0402]
view_of: []
history: []
---

## 課題

指示は「学習者の言語で」、検査は日本語だけ。

## 発見の観点

英語の学生の候補が必ず修復失敗になる現象を、エラー文から規則まで辿った。

## 解決の観点

検査側に言語判定を入れ、英語の推量形を受ける。断定形の禁止は両言語で維持。

## 一般化

生成側の指示と検査側の規則を同じ語彙で書く。
