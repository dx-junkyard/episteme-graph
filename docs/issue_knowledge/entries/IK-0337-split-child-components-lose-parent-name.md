---
id: IK-0337
title: 決定論 refinement で分割された子部品が責務名だけの機械名になり、親の意味のある名前が行から消える
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
  - docs/architecture/knowledge_structure_review_2026-09-12/A_fidelity.md
feature_context:
  realizing: 大きすぎる部品を責務ごとに分割し、コースの topic 題名や学ぶ単位の名前に使う
  layers: [pipeline_a, course_builder]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: 子の name が責務語だけで、親との関係を表す表現（親名の継承・parent 参照）が
    行に無い。接続軸: 親名は refinement_report にだけ残り、部品行・course_mapping・学ぶ単位へ
    運ばれない。処理・統制: 要素なし。
generalization:
  level: general
  general_form: 分割で生じた子に種別名だけを付け、親の固有名が派生物に継承されない
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    course_mapping に "Definition" が 2 件並ぶこと、live 部品に "Constraint" があることから
    refinement の名前生成に遡った。
resolution:
  perspective: [carry_through, single_point_fix]
  note: >-
    子の name を「親 label — 責務」にし summary にも親名を含める。stable_key の材料は
    label なので別親×同責務で衝突しない。
  landed_in:
    - src/episteme_graph/agents/component_assembly/granularity_analyzer.py
    - src/episteme_graph/agents/component_assembly/component_refiner.py
related: []
view_of: []
history: []
---

## 課題

**症状**: 学習トピック一覧に "Constraint" / "Application" / "Definition" が並び、親の
「Kernel normalization constraint」が行に無い（前回 F-5 の残り）。

**原因**: `responsibility.replace("_"," ").title()` を子の name にしていた。

## 発見の観点

再現照合（`reproduction`）と実データ（`data_inspection`）。

## 解決の観点

親名を運ぶ（`carry_through`）。名前生成の 1 箇所を直す（`single_point_fix`）。

## 一般化

分割は構造として持ち、名前は親を保つ。種別は属性で表す。
