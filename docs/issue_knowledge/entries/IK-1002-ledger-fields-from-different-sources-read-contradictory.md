---
id: IK-1002
title: "台帳の画面の欄が別々の正本から来て食い違って読める — スコープが 1 件あるのに「検証状態: 検証情報なし」、依存の広がり「最高位」の隣に「行動上の合意: 0 箇所が依存」、合意リストの同じ行に「検証: 間接的な支持あり」と「支持: 支持記録なし」"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/stakes_ledger_design.md §16.8"
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
feature_context:
  realizing: "教員が台帳と未検証合意リストの 1 行を読み、その対象がどこまで確かめられ、どれだけ依存されているかを判断する"
  layers: [doubt_d, stakes_ledger_sl, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
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
    接続軸 meaning: 検証状態（verification_status — 人の記帳か ledger_builder の既定 unknown）と検証スコープ（verification_scopes）は独立の列で、スコープの記帳は状態を変えない。依存の広がり（load_score・依存グラフの下流到達）と行動上の合意（consensus_behavioral・ledger_builder が解析時に数えた参照数で再計算されない）は別の量。支持（support_paths の支持線）と検証（verification_status）も別の量。画面は欄の意味の違いを書かず並べるので、同じことを言う欄が反対のことを言っているように読める。処理軸 wording: 「検証情報なし」は『状態が記帳されていない』を『検証の情報が無い』と言う。構造軸 representation: 状態とスコープの関係が表現に無い。
generalization:
  level: repo_pattern
  general_form: "意味の違う複数の量を、関係を説明せず同じ語彙の欄に並べるため、利用者には互いに矛盾する主張に見える"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, invariant_audit]
  note: >-
    te-04 の Cepheus ノード台帳はスコープ 1 件（DCF 法の等分配仮定）の上に「検証状態: 検証情報なし」。te-03 の合意リストから開いた主張の台帳は「依存の広がり（段階）: 最高位」と「行動上の合意: 0 箇所が依存」。te-04 の合意リスト 9・10 は「検証: 間接的な支持あり」と「支持: 支持記録なし」。run: uxsim/runs/c-r25-doubt-r5/20261027T070946Z-33422（seq2・5）/ uxsim/runs/c-r25-doubt-r5/20261027T071425Z-60143（seq9）。
resolution:
  perspective: [canonical_source, vocabulary_table]
  note: >-
    欄名の正本を label_vocab.LEDGER_FIELD_LABELS（検証状態（教員の記帳）/ 観測からの支持線 / 依存の広がり（理論操作グラフの下流・段階）/ 行動上の合意（解析時に数えた参照））に置き、台帳 GET と未検証合意リストが field_labels + field_sources_note（欄の出所が違うことの事実文）を渡す。状態が未記帳でスコープだけあるときは verification_status_note で状態とスコープの関係を言う。画面は fieldLabel() でサーバの語を描く（JS にミラーしない）。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/doubt-atlas.js"
    - "docs/manual/teacher/18-admin-doubt-atlas.md"
    - "backend/tests/test_doubt_round25_wave5.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "欄名と出所の事実文を label_vocab の 1 箇所に置き、台帳 GET と合意リストの両方がそこから渡す。"
  verification:
    methods: [guardrail]
    unverified:
      - "行動上の合意を依存の広がりと同じ再計算で更新するかは決めていない（欄名で別の量と言い分けた）"
      - "画面での読まれ方は run で確かめていない"
related: [IK-0901, IK-1001, IK-0905]
view_of: []
history: []
---

## 課題

症状: 台帳の 1 画面・合意リストの 1 行の中で、記帳あり／情報なし、依存が多い／依存なし、支持あり／支持記録なしが並ぶ。

原因: 欄ごとに正本が違う（人の記帳の状態・スコープ・依存グラフ・解析時の参照数・支持線）のに、画面はその違いを言わない。

## 発見の観点

再現と、欄どうしの意味の突き合わせ（不変条項の点検）。

## 解決の観点

第 25 周 面 B 第 5 波（2026-10-27・docs/features/stakes_ledger_design.md §16.8）で解決。欄名の正本を label_vocab.LEDGER_FIELD_LABELS（検証状態（教員の記帳）/ 観測からの支持線 / 依存の広がり（理論操作グラフの下流・段階）/ 行動上の合意（解析時に数えた参照））に置き、台帳 GET と未検証合意リストが field_labels + field_sources_note（欄の出所が違うことの事実文）を渡す。状態が未記帳でスコープだけあるときは verification_status_note で状態とスコープの関係を言う。画面は fieldLabel() でサーバの語を描く（JS にミラーしない）。

起票時の是正案: 是正案: ①検証状態が unknown でスコープがあるときは「検証状態は未記帳（スコープは 1 件記帳されています）」と状態とスコープの関係を言う ②行動上の合意は依存の広がりと同じ再計算で更新するか、欄名を「解析時に数えた参照」に変えて別の量と分かるようにする ③合意リストの「支持」は『観測からの支持線』と書き、検証の欄と語を分ける。数値は増やさない（段階と事実文のまま）。

## 一般化

別の量を同じ語で並べると、どれも正しいのに画面全体が嘘に見える。確かめ方は、1 つの対象の画面で各欄の正本を書き出し、欄名がその正本の意味を言っているかを見ること。

第 6 周の再演: GONE（説明の文・2026-10-27）。台帳の頭に「…それぞれ別の記録から読んでいます（一方が「あり」で他方が「なし」でも矛盾ではありません）。」、スコープありで状態未記帳のときは「検証状態はまだ記帳されていません（検証スコープの記帳はあります…）」が出た（Cepheus ノード 1）。残り: 欄名は付いたが「最高位」と「0 箇所が依存」の並びは IK-1030 で増える。run: uxsim/runs/c-r25-doubt-r6/20261027T122508Z-5219（seq5）/ uxsim/runs/c-r25-doubt-r6/20261027T122225Z-3508（seq5・6）。
