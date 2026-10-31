---
id: IK-0544
title: "出典の所在が節名でなく本文の書き出しで表示される（section_title 欠落）"
status: open
recorded_at: 2026-09-30
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "ペルソナ通し受講 第 14 周（uxsim/runs/c-astro-structure-30）（c-astro-structure-30）の場面"
  layers: [rag_chat, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: low
    structure: low
    connection: low
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    チャンクに section_title が無い。 未解決のため原因の座標は暫定。
generalization:
  level: repo_pattern
  general_form: "出典の所在が節名でなく本文の書き出しで表示される（section_title 欠落）"
pattern: information-dropped-as-unrepresentable
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

出典の所在が節名でなく本文の書き出しで表示される（section_title 欠落）。チャンクに section_title が無い。

## 発見の観点

第 14 周の運転記録から。

## 解決の観点

未解決。

## 一般化

未確定。
