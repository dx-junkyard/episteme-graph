---
id: IK-0350
title: 復元由来の式に付ける印「AI復元」が app.js に直書きされ、文言の正本（label_vocab）と二重になっていた
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §7.1
feature_context:
  realizing: 復元由来の式を学習者に抽出結果と同じ顔で見せない
  layers: [frontend_learning_ui, lecture_player, shared_infra]
classification:
  axes:
    processing: [wording]
    structure: [responsibility]
    connection: [none]
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
    構造軸: 学習者向け文言の正本は label_vocab（JS にミラーしない規律）なのに、教材本文の印だけが JS 側に置かれ責務が割れていた。処理軸: 文言の綴りが 2 箇所に分かれる wording の問題。確認手段: app.js のrenderMaterialEquationBody に日本語リテラルがあること。
generalization:
  level: repo_pattern
  general_form: 利用者向けの固定文言を、正本の語彙表ではなく描画側にも直書きし、二重の正本になる
pattern: duplicate-canonical-sources
discovery:
  perspective: [adversarial_review, invariant_audit]
  note: >-
    「JS に日本語をミラーしない」規律（label_vocab）と app.js の描画コードを照合した。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    label_vocab に印（RECONSTRUCTED_EQUATION_MARK）を置き、chunks.formulas の投影経路が全て通るcore/lecture.normalize_to_placeholder_format で reconstructed_mark / reconstructed_note を載せる。JS は素通し。app.js に印のリテラルが無いことをテストで固定。
  landed_in:
    - backend/core/lecture.py
    - backend/core/label_vocab.py
    - frontend/public/js/app.js
related: [IK-0328]
view_of: []
history: []
---

## 課題

**症状**: 印の文言が JS に直書きされ、文言を変えるとサーバとフロントで食い違う。

**原因**: 印を足した第 1 波で、事実文はサーバ文言にしたが短い印だけを JS に置いた。

## 発見の観点

敵対的レビュー（`adversarial_review`）で規律との照合（`invariant_audit`）。

## 解決の観点

正本を語彙表に置き（`canonical_source`）、投影の合流点で載せる。直書きの不在をテストで固定（`guardrail_fix`）。

## 一般化

固定文言は長短を問わず語彙表が正本。描画側は素通しにし、短いバッジだからと例外を作らない。
