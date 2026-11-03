---
id: IK-0541
title: "PDF 経路の教材で記号レジストリに主要な記号が登録されていない"
status: open
recorded_at: 2026-09-30
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "ペルソナ通し受講 第 14 周（uxsim/runs/c-astro-structure-30）（c-astro-structure-30）の場面"
  layers: [pipeline_a, concept_registry]
classification:
  axes:
    processing: [unknown]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: low
    structure: low
    connection: low
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 砂場の knowledge_symbols_live に主記号なし。lookup は chunks.formulas フォールバックで縮退（IK 本波の lookup 是正）。 未解決のため原因の座標は暫定。
generalization:
  level: repo_pattern
  general_form: "PDF 経路の教材で記号レジストリに主要な記号が登録されていない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "第 14 周の審判 C・頭脳メモ・製品 prompt から。"
resolution:
  perspective: [pending]
  note: "未解決。第 15 波では是正していない。"
  landed_in: []
related: []
view_of: []
history: []
---

## 課題

PDF 経路の教材で記号レジストリに主要な記号が登録されていない。砂場の knowledge_symbols_live に主記号なし。lookup は chunks.formulas フォールバックで縮退（IK 本波の lookup 是正）。

## 発見の観点

第 14 周の運転記録から。

## 解決の観点

未解決。

## 一般化

未確定（原因は仮説の段階。A層の出力を実データで辿って確かめる）。
