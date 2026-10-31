---
id: IK-0647
title: "外枠のまとまりが主題の異なる複数の手順を 1 つに束ね、前提も内側の前提を上限なしに連結して長い英文の段落として並ぶ"
status: open
recorded_at: 2026-10-04
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "学習者が外枠の理論モジュールを選び、その前提と手順を読む"
  layers: [graph_review, simulation_bridge_sb]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [none]
    governance: [budget]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 外枠の併合規則（接点が少ない限り併合）は主題を見ないため、論文中の別系統の手順が 1 つの外枠になり、前提は内側の前提の重複除去だけで上限が無い。構造軸 aggregation = 集約の単位が主題と合っていない。統制軸 budget = 前提の並びに上限が無い。処理軸 none（規則どおり）。接続軸 none。
generalization:
  level: repo_pattern
  general_form: "構造の指標だけで部分を併合し、主題の異なる手順が 1 つの単位になって、その説明も上限なく連結される"
pattern: unit-of-work-undefined
discovery:
  perspective: [reproduction]
  note: "第 21 周 (a) の再演で、初見の学生が複数系統を束ねた外枠と長い前提の段落を前に諦めた（第 21 周から残る）。"
resolution:
  perspective: [pending]
  note: "未解決。外枠を学習者には内側から見せるか、前提に上限と出所を付けるかが分かれば解ける。併合規則の変更は理論モジュール層の規則版を上げる判断になる。"
related: [IK-0608, IK-0624]
view_of: []
history: []
---

## 課題

症状: 1 つの外枠に別系統の手順が混ざり、前提が長い英文の段落で並ぶ。原因: 併合が接点の狭さだけで決まり、前提の連結に上限が無い。

## 発見の観点

ペルソナの通し受講の再演（reproduction）で残った症状。

## 解決の観点

未解決。規則版の変更か表示側の上限かの選択が要る。

## 一般化

構造の指標だけの集約は主題をまたぐ。型は unit-of-work-undefined（単位の定義が主題と合わない）。確かめ方は、複数系統の手順を持つ論文で外枠を出し、1 つの外枠が 1 つの主題に収まることを見ること。
