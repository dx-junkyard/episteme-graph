---
id: IK-0637
title: "受け取る記号に単位の記法が混じり、別の量を指す同じ記号（偏光の Q と補正係数の Q）が 1 つの記号として並ぶ"
status: open
recorded_at: 2026-10-04
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "学習者が理論モジュールの受け取る記号を読み、何を入力に与えるかを決める"
  layers: [pipeline_a, simulation_bridge_sb]
classification:
  axes:
    processing: [input_handling]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: equation_semantics の used_symbols に単位の記法（\mu\mathrm{G} 等）が入り、記号の名前空間は式をまたいで共有されるため、同じ字形の別の量が同じ記号に寄る。処理軸 input_handling = 単位の記法を記号として受け入れる。構造軸 representation = 記号に式ごとの意味を区別する属性が無い。接続軸 meaning = 同じ名前が別の量を指す。統制軸 none。表示側は分野語なしでは除けない。
generalization:
  level: repo_pattern
  general_form: "式の記号を字形だけで束ね、単位の記法や別の量が同じ記号として並ぶ"
pattern: same-name-different-referents
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 22 周の学生場面 (a')(e'') で、受け取る記号に単位や同名の別量が並んだ。A層の記号表の used_symbols を確かめた。"
resolution:
  perspective: [pending]
  note: "未解決。A層（equation_semantics / symbol_registry）で単位の記法を記号から外し、記号に式ごとの意味（kind）を持たせれば解ける。記号表はほぼ全件 kind unknown で、表示側の判定は分野語のハードコードになるため採らない。"
related: [IK-0627, IK-0608]
view_of: []
history: []
---

## 課題

症状: 受け取る記号に単位の記法が出て、同じ字形の別の量が 1 つに並ぶ。原因: A層の used_symbols が単位の記法を含み、記号の名前空間が式をまたいで衝突する。

## 発見の観点

ペルソナの通し受講（reproduction）で混乱した場面から、記号表のデータを確かめた（data_inspection）。

## 解決の観点

未解決。A層の記号抽出と記号の意味の付与が要る。表示側で分野の単位語を除く案は domain-independent の原則に反する。

## 一般化

字形だけで記号を同定すると、単位や別の量が混ざる。型は same-name-different-referents（人間向けの呼称の衝突）。確かめ方は、同じ字形で別の量を持つ論文で記号一覧を出し、区別されることを見ること。
