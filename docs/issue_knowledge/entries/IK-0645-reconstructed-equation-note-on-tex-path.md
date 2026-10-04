---
id: IK-0645
title: "TeX 経路の教材でも多くのまとまりに「入力の式に PDF から復元された式が含まれています」が付き、文言も PDF 経路に固定されている"
status: open
recorded_at: 2026-10-04
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "学習者が理論モジュールを選ぶとき、入力の式の出所（原文か復元か）の注記を読む"
  layers: [pipeline_a, simulation_bridge_sb]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: equation_semantics の inline 式候補が TeX 経路でも reconstructed 扱いになり、codegen._is_reconstructed がそれを拾って注記を出す。注記の文言は PDF からの復元を前提にしている。処理軸 logic = 出所の判定が経路を見ない。構造軸 representation = 「復元」が経路ごとの意味を持たない 1 つの印。接続軸 meaning = 印が TeX では別の意味（inline 候補）で読まれる。TeX 教材の equation_semantics artifact で reconstructed の付いた式の出所を確かめれば確定する。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "出所の印を入力経路を見ずに付け、別の経路では意味の違う印を同じ注記で見せる"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction]
  note: "第 22 周の再演で、TeX 経路の論文でも多くのまとまりに PDF 復元の注記が付いた（第 21 周以前からの既知の派生）。"
resolution:
  perspective: [pending]
  note: "未解決（A層由来）。inline 候補を復元と区別する印を A層で持たせ、注記の文言を経路から組めば解ける。"
related: [IK-0608]
view_of: []
history: []
---

## 課題

症状: TeX の論文で PDF からの復元の注記が多くのまとまりに付く。原因（仮説）: inline 式候補が経路を問わず復元扱いになる。

## 発見の観点

ペルソナの通し受講（reproduction）で、TeX 教材に PDF の注記が出るのを観測した。

## 解決の観点

未解決。A層の出所の印を経路ごとに分ける必要がある。

## 一般化

出所の印が経路を見ないと、正しい経路の成果にも警告が付き、警告が読まれなくなる。確かめ方は、TeX 経路と PDF 経路の同じ論文で注記の付くまとまりを比べること。
