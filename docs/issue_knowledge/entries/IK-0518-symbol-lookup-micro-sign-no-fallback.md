---
id: IK-0518
title: "記号の lookup で µ（U+00B5）と μ を別記号として扱い、記号レジストリに無い記号は教材の式に現れていても「定義なし」だけを返していた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "教材の数式の記号をタップして定義を見る"
  layers: [concept_registry, learner_experience_b]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    互換字形を正規化し、chunks.formulas からの決定論フォールバック（registered:false・印字番号のみ）を足した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "字形の異なる同一文字を正規化せずに照合し、主たる索引が空のとき手元の別資料を見ない"
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [reproduction, data_inspection]
  note: "砂場の knowledge_symbols_live に主記号が無く、µ の lookup が全件空だった。"
resolution:
  perspective: [single_point_fix]
  note: "互換字形を正規化し、chunks.formulas からの決定論フォールバック（registered:false・印字番号のみ）を足した。"
  landed_in:
    - backend/core/symbol_lookup.py
    - backend/tests/test_symbol_lookup_core.py
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

記号 lookup が字形違いと未登録で空になっていた。

## 発見の観点

砂場の knowledge_symbols_live に主記号が無く、µ の lookup が全件空だった。

## 解決の観点

互換字形を正規化し、chunks.formulas からの決定論フォールバック（registered:false・印字番号のみ）を足した。

## 一般化

字形の異なる同一文字を正規化せずに照合し、主たる索引が空のとき手元の別資料を見ない。
