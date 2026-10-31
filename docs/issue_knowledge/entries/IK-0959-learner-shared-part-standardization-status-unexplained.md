---
id: IK-0959
title: "学生の「共通部品として」に「標準化状況: 未評価」と教員側の管理列の値をそのまま出し、学生には意味が分からない"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が部品の文脈から、教員が確かめた共通部品を読む"
  layers: [learner_experience_b, concept_registry, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
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
    接続軸 meaning: standardization_status は教員が W層の標準化判定を commit したときだけ動くガバナンス列（library_entries）で、既定は
    unknown。core/component_context.py:668-671 がそれを学生 DTO に入れ、frontend/public/js/app.js:8628-8631 が
    MATERIAL_LIBRARY_STATUS_LABELS で「標準化状況: 未評価」と描く。教員の内部手続の状態が説明なしに学生へ渡る。処理軸 wording: 語そのものが学生向けでない。
generalization:
  level: repo_pattern
  general_form: "内部手続の状態の値を、意味の説明なしに別の読み手（学習者）の画面へ素通しする"
pattern: projection-mistaken-for-source
discovery:
  perspective: [reproduction]
  note: >-
    学生 st-08 と st-01 がどちらも「標準化状況: 未評価は何のことか分からない」と記した（uxsim/runs/c-astro-r25-share-r4/20261024T233212Z-21482
    seq11・uxsim/runs/c-astro-r25-share-r4/20261024T233359Z-43631 seq8）。
resolution:
  perspective: [vocabulary_table]
  note: >-
    標準化の評価を学生向けの 1 文にした。label_vocab.LEARNER_STANDARDIZATION_NOTES / learner_standardization_note が教員の確定した 4 値だけを文にし、未評価（unknown）は空 = キーを作らない。component_context は standardization_status の生値を学生 DTO に入れず standardization_note だけを載せ、app.js は語表で引かずに素通しで描く。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/core/component_context.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_component_context_core.py"
    - "backend/tests/test_share_round25_identity_consumers.py"
  verification:
    methods: [guardrail]
    unverified:
      - "評価済みのエントリでの学生画面"
related: [IK-0920]
view_of: []
history: []
---

## 課題

症状: 学生の共通部品のタブに「標準化状況: 未評価」が出る。原因: 教員側の管理列を学生 DTO に入れ、ラベルだけ付けて描いている。

## 発見の観点

再現（学生 2 人の (d)(e)）。

## 解決の観点

是正済み（第 25 周・面 A 第 4 波・2026-10-25）。

旧案: 是正案: unknown のときは学生には出さない。評価済みのときだけ学生向けの 1 文（例「この分野で広く使われている標準の考え方です」）を label_vocab に置いて出す。

## 一般化

内部の値を投影と取り違える型。確かめ方は、unknown と評価済みのエントリで学生のタブを開き、unknown では行が無く、評価済みでは学生向けの文が出ることを見ること。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 消滅（学生の部品の文脈に「標準化状況: 未評価」が出ない）。教員のエントリ詳細の「標準化度: 未評価」は教員向けで設計どおり。run: uxsim/runs/c-astro-r25-share-r5/20261027T071307Z-52079 seq11、uxsim/runs/c-astro-r25-share-r5/20261027T071135Z-43804 seq4
