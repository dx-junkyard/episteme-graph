---
id: IK-0524
title: "component-graph 応答のノードラベルに式の内部 ID（eq_...）がそのまま出ていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/features/theory_module_layer_design.md
feature_context:
  realizing: "グラフレビューで式の詳細ノードを読む"
  layers: [graph_review]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
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
    読み時に mask_equation_ids_in_label で「式 (N)」へ置換（graph_json は非改変）。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "内部 ID をラベルに埋め込む生成側の出力を、表示側が印字番号に読み替えない"
pattern: id-namespace-conflated
discovery:
  perspective: [data_inspection]
  note: "投影のノード名に eq_ 形の ID が並んだ。"
resolution:
  perspective: [single_point_fix]
  note: "読み時に mask_equation_ids_in_label で「式 (N)」へ置換（graph_json は非改変）。"
  landed_in:
    - backend/api/routes/theory_components.py
    - backend/tests/test_graph_review_detail_label_mask.py
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

ラベルに式 ID が出た（PL7 違反）。

## 発見の観点

投影のノード名に eq_ 形の ID が並んだ。

## 解決の観点

読み時に mask_equation_ids_in_label で「式 (N)」へ置換（graph_json は非改変）。

## 一般化

内部 ID をラベルに埋め込む生成側の出力を、表示側が印字番号に読み替えない。
