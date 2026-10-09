---
id: IK-0656
title: "わたしの記録が空の系統を並べ、差し替えた行も有効な行と同じ見た目で、設計メモ調の文と内部語の系統名を出す"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-09
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/trace_registry_sovereignty_ledger_design.md"
feature_context:
  realizing: "学習者がわたしの記録で、自分の記録のうち中身のあるものと差し替え済みのものを見分ける"
  layers: [trace_registry, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
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
    確認: 一覧は登録簿の全系統を宣言順に出し空の系統にも定型文を出していた（my-records.js / trace_ledger.py）。差し替え・候補の行にはクラスが付くが CSS が無かった。常設の文は「…設計中です」という設計の状態で、系統名は「軽量アンカー」「学習の意図」という内部の呼称だった（trace_registry.py の label）。処理軸 wording = 画面の語と違う呼称・事実でない文。構造軸 representation = 登録簿の label が内部の呼称で、利用者向けの名前の居場所になっていない。接続軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "内部の宣言をそのまま利用者向けの一覧に流し、中身の無い項目・内部の呼称・設計の状態が画面に出る"
pattern: wording-mismatch
discovery:
  perspective: [invariant_audit]
  note: "第 23 周の机上レビューで、わたしの記録の描画と登録簿の宣言、画面の他の語（教材の「印」）を読み合わせて見えた。"
resolution:
  perspective: [single_point_fix, vocabulary_table]
  note: "中身のある系統だけ見出しごと出して空の系統は名前を 1 行に畳み、差し替え・候補の行に CSS を足し、常設の文を現物の事実に直し、登録簿の系統名を画面の語に揃えた。"
  landed_in:
    - "frontend/public/js/my-records.js"
    - "frontend/public/css/styles.css"
    - "backend/core/trace_registry.py"
    - "backend/tests/test_my_records_ui_static.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場・ブラウザでの空状態と差し替え行の見た目"
related: [IK-0653]
view_of: []
history: []
---

## 課題

症状: 空の系統の定型文が並び、差し替えた行が有効な行と見分けられず、「封印の仕組みは…設計中です」と内部の系統名が出る。原因: 登録簿の宣言（内部の呼称・全系統）をそのまま描き、状態の見た目を用意していなかった。

## 発見の観点

画面の描画 × 登録簿の宣言 × 画面の他の語の読み合わせ（invariant_audit）。

## 解決の観点

描画と系統名の局所の是正（single_point_fix）と、系統名の正本を登録簿の label に保ったまま画面の語へ揃えた（vocabulary_table）。

## 一般化

内部の宣言を利用者向けにそのまま流すと、中身の無いものと内部の言葉が画面を占める。確かめ方は、記録の無い利用者と差し替え行のある利用者で一覧を開き、空の系統が畳まれ差し替え行が区別されることを見ること。
