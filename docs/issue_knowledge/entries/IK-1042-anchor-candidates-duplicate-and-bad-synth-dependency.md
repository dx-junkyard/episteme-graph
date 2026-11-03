---
id: IK-1042
title: "問いの帰属候補に、同じ式由来の主張が 2 件重複し（「An equation of this paper defines $F_{EOS}$」）、式由来の合成主張が 0 や L_BC を変数として依存に数え（「m(r) depends on r, 0, L_BC」）、概念のラベルが英語のまま学習者のダイジェストに出る"
status: open
recorded_at: 2026-10-27
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22"
  - "docs/features/focus_document_design.md"
feature_context:
  realizing: "学生の問いを構造（主張・概念）に帰属させ、本人の確認のダイジェストで帰属先を読む"
  layers: [learner_experience_b, pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 処理軸 input_handling — 式由来の主張の合成（equation_claim_synthesis）が式の右辺の数値と境界条件の記号を変数として列挙し、
    同じ定義式が版ごと・式ごとに同文の主張を作る。帰属の候補（structure_anchor の input_builder）は同文を畳まない。概念ラベルは
    英語の主張本文から取られる。確定には、該当論文の claim_object_builder artifact の合成主張と帰属の入力を見ることが要る。
generalization:
  level: repo_pattern
  general_form: "式から機械的に合成した文が、数値や境界条件を変数として扱い、同文を重複させたまま候補に並ぶ"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: >-
    製品頭脳 A 4252（structure_anchor 帰属）で、候補の claims の重複・不正な依存・英語の concept ラベルを記した。
resolution:
  perspective: [pending]
  note: >-
    第 6 波では記帳のみ。根は A層の式由来の主張の合成（変数の抽出）と帰属の候補の組み立ての 2 箇所にまたがり、A層の変更は
    再解析を要する（既存の合成主張は行として残る）。帰属の候補の同文の畳み込みだけなら小さいが、不正な依存を残したまま
    畳んでも誤った候補は消えないので、合成の規則と併せて扱う。
  landed_in: []
related: [IK-0571]
view_of: []
history: []
---

## 課題

症状: 帰属の候補に同じ主張が 2 件並び、「0」を変数とする誤った依存を持つ主張が混じり、本人のダイジェストに英語のラベルが出る。

## 発見の観点

製品頭脳に渡った帰属のプロンプトの点検。

## 解決の観点

未解決（原因は仮説のまま — 合成主張の artifact を見ていない）。式由来の主張の合成で数値・境界条件を変数から外す規則と、候補の同文の畳み込みを併せて設計する。

## 一般化

機械合成の文の質が候補に持ち込まれる型。確かめ方: 境界条件を含む式から合成した主張の依存に数値が出ないこと、同文が 1 件に畳まれることを見る。
