---
id: IK-0538
title: "カートリッジ同梱の骨格（particle_physics）に分野の表示名を宣言する場所が無く、「名前が登録されていない分野」と表示されていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "分野の地図で分野名を見る"
  layers: [field_atlas_s, cartridges]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
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
    atlas_store が cartridges/<id>/atlas/domain.json も読むようにし、particle_physics に置いた。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "表示名の供給源がある経路にしか無い"
pattern: referenced-source-does-not-exist
discovery:
  perspective: [data_inspection]
  note: "投影の分野名が空。"
resolution:
  perspective: [canonical_source]
  note: "atlas_store が cartridges/<id>/atlas/domain.json も読むようにし、particle_physics に置いた。"
  landed_in:
    - backend/core/atlas_store.py
    - backend/cartridges/particle_physics/atlas/domain.json
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

分野名が無かった。

## 発見の観点

投影の分野名が空。

## 解決の観点

atlas_store が cartridges/<id>/atlas/domain.json も読むようにし、particle_physics に置いた。

## 一般化

表示名の供給源がある経路にしか無い。
