---
id: IK-0341
title: 主張参照の接頭辞（claim:）が参照側と索引側で食い違い、開幕画面に内部 ID が露出し thesis 参照が全件未解決になる
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
feature_context:
  realizing: 中心命題・支持構造・DSL の主張参照を DB の主張行に解決し、学習者・教員に本文で見せる
  layers: [discuss, graph_review, export_bundle, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [meaning]
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
    接続軸: 同じ主張を指す参照の綴りが段階間で違い（`claim:` 有無）、意味は同じなのに
    解決しない。構造軸: 参照の正規化が 3 箇所（開幕・グラフレビュー・export gate）で
    別々に書かれ正本が無い。処理・統制: 要素なし。
generalization:
  level: general
  general_form: 同じ対象を指す参照の綴りに名前空間の接頭辞が付いたり付かなかったりし、正規化の正本が無いため経路ごとに未解決になる
pattern: id-namespace-conflated
discovery:
  perspective: [data_inspection, reproduction]
  note: >-
    開幕画面の item の 27〜68% が label = 内部 ID であること、thesis の missing_claim が
    接頭辞を落とすと全件解決することを実データで確認した。
resolution:
  perspective: [canonical_source, single_point_fix]
  note: >-
    正規化の純関数を knowledge_objects に 1 つ置き、3 経路から使う。索引側にも接頭辞付き
    キーを登録する。
  landed_in:
    - backend/core/knowledge_objects/references.py
    - backend/core/discuss/opening.py
    - backend/api/routes/theory_components.py
    - backend/core/document_pipeline/export_validation_gate.py
related: [IK-0303, IK-0339]
view_of: []
history: []
---

## 課題

**症状**: 学習者向け開幕画面に `claim:blk_9fd917ff:span_001` が並ぶ。thesis_coverage の
missing_claim 19〜27 件。export の DSL_EDGE_DANGLING_EVIDENCE 20 件。

**原因**: 参照側は `claim:` 付き、legacy_ids は無し。正規化が経路ごとに別実装。

## 発見の観点

実データ（`data_inspection`）と再現照合（`reproduction`）。

## 解決の観点

正本を 1 つに（`canonical_source`）。

## 一般化

名前空間の接頭辞は「付ける側」と「引く側」で必ず同じ正規化を通す。
