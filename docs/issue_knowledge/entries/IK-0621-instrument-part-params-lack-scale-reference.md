---
id: IK-0621
title: "実験機の部品のパラメータ（雑音の大きさなど）に単位と基準が無く、理論の出力の桁と結び付かないため、既定値の雑音が出力を埋めて「乖離」「埋もれる」と判定される"
status: open
recorded_at: 2026-10-03
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "学習者が部品を配線し、理論の出力に雑音や処理をかけた模擬観測を作る"
  layers: [simulation_bridge_sb, image_library_l, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: backend/library_common/instrument_parts.json の雑音部品の sigma など多くの params は unit が空で、「理論の出力と同じ単位の絶対値」といった基準の注記も部品目録の DTO に無い。極めて小さい桁の理論出力に既定の雑音を足し、比較器が乖離・分離不能を出した。改訂案は桁合わせを正しく提案した。接続軸 meaning = 部品のパラメータと理論出力の単位・尺度が結び付かない。構造軸 representation = 単位・基準の居場所が空。処理軸 none。統制軸 none。
generalization:
  level: general
  general_form: "尺度に依存する既定値を、適用先の尺度を問わず同じ値で当て、適用先によっては既定値だけで判定が片側に倒れる"
pattern: threshold-reused-across-regimes
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、配線の改訂を試した場面で、微小な桁の理論出力に既定の雑音が足され、結果が乖離になった。"
resolution:
  perspective: [pending]
  note: "部分着地（第 21 波・2026-10-03）: simulation-lab.js が単位の空いた部品パラメータに一度だけ「（理論の出力と同じ単位の絶対値）」の注記を出す（DTO に note があれば優先）。部品の DTO に note は無く、目録の本文の単位・基準も変えていないため open のまま（設計書 §12.4 の据え置き）。"
  landed_in:
    - frontend/public/js/simulation-lab.js
    - "docs/features/simulation_bridge_design.md §12.4"
related: [IK-0606]
view_of: []
history: []
---

## 課題

症状: 配線した模擬観測が雑音に埋もれ、乖離・分離不能と出る。原因: 部品のパラメータに単位と基準が無く、学習者も生成側も理論出力の桁に合わせるべきことが分からない。既定値は特定の桁を暗黙に想定している。

## 発見の観点

ペルソナの通し受講（reproduction）で観測し、部品目録の params と比較結果を突き合わせた。

## 解決の観点

部分着地（第 21 波）。画面に注記だけを置いた。部品の雑音の単位・基準は目録の本文の問題で、据え置き。

## 一般化

尺度に依存する値は単位と基準とセットで持たないと、適用先が変わると意味が変わる。型は threshold-reused-across-regimes。確かめ方は、桁の大きく異なる理論出力に同じ部品を配線し、欄の注記だけで学習者が値を合わせられるかを見ること。
