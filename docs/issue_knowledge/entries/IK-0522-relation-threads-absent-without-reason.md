---
id: IK-0522
title: "推定の糸を出せないとき API がキーごと落とし、学習者には糸のトグルが効かない理由が分からなかった"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/features/atlas_relation_edges_design.md
feature_context:
  realizing: "分野の地図で推定の糸を表示する"
  layers: [relation_edges_re, field_atlas_s]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    threads: {available:false, note} を返し、理由文を threads_unavailable_note の 3 種にした。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "fail-soft でキーを落とすと、使う側からは不具合と区別できない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "砂場の atlas_anchor_embeddings が 0 行で、threads が常に欠けていた。"
resolution:
  perspective: [explicit_contract]
  note: "threads: {available:false, note} を返し、理由文を threads_unavailable_note の 3 種にした。"
  landed_in:
    - backend/core/atlas_edges/threads.py
    - backend/api/routes/atlas_view.py
    - backend/tests/test_atlas_edges_core.py
    - backend/tests/api/test_atlas_view_api.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

糸を出せない理由が無言だった。

## 発見の観点

砂場の atlas_anchor_embeddings が 0 行で、threads が常に欠けていた。

## 解決の観点

threads: {available:false, note} を返し、理由文を threads_unavailable_note の 3 種にした。

## 一般化

fail-soft でキーを落とすと、使う側からは不具合と区別できない。
