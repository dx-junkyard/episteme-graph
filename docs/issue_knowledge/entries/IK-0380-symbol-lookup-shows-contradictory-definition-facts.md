---
id: IK-0380
title: "数式の記号の「直前の定義」が、定義なしのときに「使用のみ（定義は別の箇所）」と「この論文には定義の記述が見つかりませんでした」を同時に並べるため、定義が別の場所にあるのか無いのかが読めない"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が式の記号をタップして定義を見る（概念レジストリ P3-5）
  layers: [concept_registry, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
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
    処理軸: 2 つの事実文が同じ facts 配列に並び、片方（使用のみ・定義は別の箇所）が「別の箇所にある」と含意し、
    もう片方（定義の記述が見つからない）が「無い」と言う（wording）。接続軸: 「使用のみ」は記号行の役割ラベルで、
    「定義なし」は検索結果の事実で、意味の違う 2 系統の文を同じ欄に運んでいる（meaning。medium: 役割ラベルの出所は
    本エントリでは追っていない）。確認: 第 7 周で学生 2 名の symbol lookup（alpha）の DTO が
    facts: ["使用のみ（定義は別の箇所）", "この論文には定義の記述が見つかりませんでした。"] を返し、両名が矛盾として記録。
    直前のチャットは「§3.3 で P∝I^-α と定義」と答えていた。
generalization:
  level: repo_pattern
  general_form: 出所の違う事実文を同じ欄に並べ、含意が食い違う
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: 懐疑派と入門者の 2 名が同じ記号で同じ画面を見て、同じ矛盾を記録した。
resolution:
  perspective: [single_point_fix]
  note: >-
    定義の逐語が見つからないとき、定義状態のラベルは FACT_NO_DEFINITION と含意が食い違わない
    definition_missing（「定義なし」）のときだけ添えるようにした。used の「使用のみ（定義は別の箇所）」や
    defined の「この論文で定義」は出さない。訳語は element_vocab.DEFINITION_STATUS_LABELS のまま
    （新しい語彙・JS ミラーは足していない）で、symbol_lookup 側は許すキーの集合だけを持つ。
  landed_in:
    - backend/core/symbol_lookup.py
    - backend/tests/test_symbol_lookup_core.py
    - docs/features/concept_registry_design.md
  verification:
    methods: [guardrail]
    unverified:
      - 砂場・ブラウザでの記号タップ（第 7 周の alpha の再操作）
      - 記号登録簿の definition_status 自体が本文の定義と食い違う件（チャットは §3.3 で定義と答えていた。定義の逐語を拾えなかった側の原因は追っていない）
related: []
view_of: []
history: []
---

## 課題

記号の定義なしのとき、相反する 2 文が並ぶ。

## 発見の観点

実ペルソナ 2 名の同一観測。

## 解決の観点

定義なしの事実文と食い違う定義状態のラベルを出さない（許すキーの集合を symbol_lookup に置き、訳語は element_vocab のまま）。

## 一般化

出所の違う事実文の同居。
