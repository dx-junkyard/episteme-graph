---
id: IK-0681
title: "TeX 経路の論文の式に印字の式番号が無く、表示名が TeX のラベル（eq:mu・eq:cl_kk）になり、コースビルダーも「内部の識別子しか無い」と答える"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/pipeline/overview.md"
  - "docs/features/display_projection_design.md"
feature_context:
  realizing: "教員・学習者が TeX 経路の論文の式を、原文の式番号で照合する"
  layers: [pipeline_a, course_builder, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 砂場 DB の chunks.formulas[].label は TeX の \label の鍵（eq:cl_kk）か空で印字番号が無いこと（確認済みの事実）から、TeX には番号そのものが書かれておらず（組版で数える）、取り込みが式の出現順から番号を数えていない。構造軸 representation = 印字番号を表す値が TeX 経路で作られない。接続軸 information = 番号が表示名・コースビルダーの文脈へ届かない。TeX の式環境の出現順と PDF の印字番号を突き合わせれば確定する。
generalization:
  level: repo_pattern
  general_form: "入力の形式によっては表示に要る値が原文に書かれておらず、取り込みが導出しないまま内部の鍵が表示名に繰り上がる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 23 周の砂場で、几帳面な教員が「式番号は内部の識別子しか無いと回答された」、学生が「式の見出しに eq:mu のようなラベル」と書き、DB の式ラベルを読んで確かめた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（A層の入力・core）: TeX の番号付き式環境の出現順から印字番号を数えて式に持たせ、表示名の正本（equation_display_label）が番号を使えるようにする。"
related: [IK-0645]
view_of: []
history: []
---

## 課題

症状: TeX 経路の式に原文の番号が無く、TeX の鍵が表示名に出る。原因（仮説）: 取り込みが番号を数えない。

## 発見の観点

ペルソナの通し受講（reproduction）と DB の読み取り（data_inspection）。

## 解決の観点

未解決。取り込み段で番号を導出する。

## 一般化

表示に要る値が原文に書かれない形式では、取り込みで導出しないと内部の鍵が表示名になる。確かめ方は、番号付き式を含む TeX を取り込み、式の表示名が原文の番号になることを見ること。
