---
id: IK-0526
title: "論文層の章アウトラインで、式や本文の一文を見出しと誤認した節が独立した章として並んでいた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/features/graph_paper_layer_design.md
feature_context:
  realizing: "グラフを論文の章の順で読む"
  layers: [graph_paper_layer]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    is_heading_like_title で誤認見出しを直前の章へ畳み、folded_section_ids に残す。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "構造解析の見出し判定をそのまま信じ、見出しらしさを表示側で確かめない"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "投影に式だけの「章」が出た。"
resolution:
  perspective: [single_point_fix]
  note: "is_heading_like_title で誤認見出しを直前の章へ畳み、folded_section_ids に残す。"
  landed_in:
    - backend/core/graph_paper_layer/builder.py
    - backend/tests/test_graph_paper_layer_core.py
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

誤認見出しが章になっていた。

## 発見の観点

投影に式だけの「章」が出た。

## 解決の観点

is_heading_like_title で誤認見出しを直前の章へ畳み、folded_section_ids に残す。

## 一般化

構造解析の見出し判定をそのまま信じ、見出しらしさを表示側で確かめない。
