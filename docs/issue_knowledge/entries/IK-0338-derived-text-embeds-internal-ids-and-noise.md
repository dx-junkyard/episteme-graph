---
id: IK-0338
title: 式由来の合成主張・記号候補・概念名に内部 ID・数値リテラル・記号様の名前・自己依存が焼き込まれる
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
feature_context:
  realizing: 式から主張・記号・概念を派生させ、学習者向けの本文と概念一覧に出す
  layers: [pipeline_a, knowledge_objects, concept_registry]
classification:
  axes:
    processing: [input_handling, wording]
    structure: [none]
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
    処理軸: 候補の形式ゲート（数値・環境名・参照語・長さ）が無く、本文生成が内部 ID を
    印字番号の代わりに使い、記号でない語を数式区切りで囲う（入力の取り扱いと文言）。
    構造軸: 記号か概念かの判定表は既存の正本（is_symbol_like_concept_name）があり
    分散ではないので none（ただし穴があったので medium）。接続・統制: 要素なし。
generalization:
  level: repo_pattern
  general_form: 派生テキストの生成が内部識別子と形式ゲート無しの候補をそのまま本文・一覧に流す
pattern: wording-mismatch
discovery:
  perspective: [data_inspection]
  note: >-
    主張本文の "In equation (eq_eqcand_inline_…), X depends on X" と記号レジストリの
    "0.015" / "\beginpmatrix…" を実データで確認した。
resolution:
  perspective: [single_point_fix, fail_closed]
  note: >-
    印字番号が無ければ中立語、自己依存は除外、数式区切りは記号様の名前だけ。記号候補は
    形式ゲートで弾き除外理由を coverage に残す。概念名の記号判定に関数形・LaTeX 含有・
    長さを追加。
  landed_in:
    - src/episteme_graph/agents/claim_object_builder/equation_claim_synthesis.py
    - src/episteme_graph/agents/symbol_registry/builder.py
    - src/episteme_graph/agents/component_assembly/schema.py
related: [IK-0103]
view_of: []
history: []
---

## 課題

**症状**: 主張本文に内部 ID と「X が X に依存」、記号レジストリに数値リテラル・環境名、
概念に `w(\theta)` が出る。学習者射影を通っても消えない（本文に焼き込まれている）。

**原因**: 生成側に形式ゲートが無く、呼称に内部 ID を使う。

## 発見の観点

実データ（`data_inspection`）。

## 解決の観点

生成箇所の是正（`single_point_fix`）と、判定できない候補は通さない（`fail_closed`）。

## 一般化

派生テキストは表示に届く前提で作る。内部 ID は表示ラベルにしない（PL7）。
