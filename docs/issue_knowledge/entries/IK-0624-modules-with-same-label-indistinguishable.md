---
id: IK-0624
title: "理論モジュールのラベルを「工程 + 理論対象」だけで作るため、同じ記号を扱う別のモジュールが同じ文面で並び、番号以外に区別できない"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "学習者と教員が理論モジュールの一覧から目的のモジュールを選ぶ"
  layers: [simulation_bridge_sb, graph_review, frontend_learning_ui, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 理論モジュールのラベルは工程の動詞列と理論対象（記号）から作られ、同じ記号を扱う別のモジュールは同じ文面になる。学生場面では同じ記号のモジュールが複数、教員場面では「制約を適用」の同文が複数並んだ。構造軸 representation = 同名の項目を区別する属性がラベルに入らない。接続軸 meaning = 同じ文面が別の部分系を指す。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "項目の表示名を少数の属性から作り、同じ属性を持つ別の項目が同じ表示名になって区別できない"
pattern: same-name-different-referents
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、学生と教員の両場面で、同じ文面のモジュールが並び、どれを選べばよいか迷った。"
resolution:
  perspective: [representation_change]
  note: "学習画面の一覧に番号（「3. …」）を付け、選んだまとまりを番号つきで「選んだまとまり: …」と表示して同じ題名のまとまりを見分けられるようにした。"
  landed_in:
    - frontend/public/js/simulation-lab.js
    - backend/tests/test_simulation_lab_ui_static.py
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail, docker_e2e]
    unverified:
      - "教員画面の一覧には番号が無く、教員の再演では同じ文面の 3 つがなお区別できなかった"
      - "番号を付けても同じ文面の題名そのものは残る"
related: [IK-0608]
view_of: []
history: []
---

## 課題

症状: 一覧に同じ文面のモジュールが複数並び、番号以外で区別できない。原因: ラベルが工程と理論対象だけで作られ、扱う式や段の違いが文面に出ない。

## 発見の観点

ペルソナの通し受講（reproduction）で学生・教員の両方に現れた。

## 解決の観点

学習画面では番号で見分けられるようにした。教員画面の同じ文面の並びは再演でも残った。

## 一般化

同名の項目を区別する属性を表示に持たない一覧は、選択を誤らせる。型は same-name-different-referents。確かめ方は、同じ記号を扱うモジュールが複数ある教材で一覧を出し、区別の手がかりが画面にあることを見ること。
