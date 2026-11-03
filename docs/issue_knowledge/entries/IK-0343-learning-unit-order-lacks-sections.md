---
id: IK-0343
title: 学ぶ単位の order_index が種別内 0 始まりで section_ids も 2 種別にしか無く、単位の並びだけでは論文の章順を再現できない
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
  - docs/features/learning_units_design.md
feature_context:
  realizing: 学ぶ単位の候補を論文の順に並べ、教員が topic に束ねる
  layers: [learning_units, course_builder]
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
    構造軸: 並びの表現が種別ごとの局所順で、文書全体の順序を表せない。接続軸: 章の
    帰属（section）は材料（support entry → claim → section）から導けるのに 3 種別で
    運ばれていない。処理・統制: 要素なし。
generalization:
  level: general
  general_form: 異種の項目に局所的な連番しか持たせず、共通の並び軸（章）を伝播しないため、全体の順序を再構成できない
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    section_block だけを並べると論文の議論の弧になるが、5 種別を混ぜると種別ごとの
    ブロックになることを実データで確認した。
resolution:
  perspective: [carry_through, representation_change]
  note: >-
    thesis_support / parent_component / dsl_node に section_ids を決定論伝播し、候補順を
    （章の順 → 種別 → order_index）にする。複数章にまたがる材料は引かない。
  landed_in:
    - backend/core/knowledge_objects/learning_units.py
    - backend/core/course_units.py
  verification:
    methods: [guardrail]
    unverified:
      - 是正後の再解析による実データでの効果の実測（LLM の live 呼び出しを行わない方針のため次回の解析待ち）
      - docker で組み上げた実機での E2E
related: [IK-0113]
view_of: []
history: []
---

## 課題

**症状**: 候補一覧が種別ごとの 5 ブロックになり章順に並ばない。

**原因**: order_index が種別内 0 始まりで、section_ids が section_block と figure にしか無い。

## 発見の観点

再現照合（`reproduction`）と実データ（`data_inspection`）。

## 解決の観点

章の帰属を運び（`carry_through`）、並びの表現を全体順にする（`representation_change`）。

## 一般化

種別を跨ぐ一覧には共通の並び軸を持たせる。局所連番だけでは全体が再構成できない。
