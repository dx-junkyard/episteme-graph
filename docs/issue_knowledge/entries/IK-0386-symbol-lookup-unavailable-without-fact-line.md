---
id: IK-0386
title: "記号の「直前の定義」が、記号の登録が無いとき・照会できる論文が無いときに available: false だけを返し、何が無いのかを言う事実文を1つも持たない"
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
    processing: [logic]
    structure: [none]
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
    処理軸: lookup_symbol_definition の早期 return 2 経路（照会できる論文が無い / 記号行が1つも一致しない）が
    facts を空のまま返していた（logic。定義なしの経路だけが FACT_NO_DEFINITION を持っていた）。接続軸: 「なぜ無いのか」
    （登録が無い・論文が無い）という情報が DTO へ運ばれず、学習者には available: false しか届かない（information。medium:
    学習画面 app.js は available: false のときサーバの facts を読まず固定文を出すため、画面上の見え方は経路ごとに違う）。
    確認: 第 8 周で学生 2 名が B_{3D} を照会し {"available": false, "symbol": "B_{3D}", "facts": []} を受け取り
    「定義が無いのか調べ方が悪いのか分からない」と記録。
generalization:
  level: repo_pattern
  general_form: 「無い」という結果を返すとき、何が無いのかを結果の形に載せる場所が無く、理由が黙って落ちる
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: 同じ記号を 2 名が照会し、同じ空の事実文を見た。
resolution:
  perspective: [single_point_fix]
  note: >-
    available: false を返すすべての経路が事実文を正確に1つ持つようにした。記号の登録が無い（または正規化して空になる）
    ときは FACT_SYMBOL_NOT_REGISTERED「このコースの論文には、この記号の登録がありません。」、照会できる論文が無いときは
    FACT_NO_SOURCE_DOCUMENTS。主語はコースの論文（閉世界。分野レベルの不在は言わない = KR8）・数値なし。
    記号が登録されていたかどうか自体（B_{3D} が登録簿に無かったのか、`B_{\rm 3D}` のような表記で照合を外していたのか）は
    砂場の DB を見ていないので確かめていない。後者に備えて照合キーは書体指定（\rm / \mathrm 等）を畳むようにした（IK-0387）。
  landed_in:
    - backend/core/symbol_lookup.py
    - backend/tests/test_symbol_lookup_core.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での B_{3D} の再照会（登録簿に行があったか・どの表記で登録されていたか）
      - "学習画面 app.js openSymbolLookup は available: false のときサーバの facts を使わず固定文を描く（フロントは未変更。サーバの facts を優先して描く変更は別担当）"
related: [IK-0380, IK-0387]
view_of: []
history: []
---

## 課題

記号の照会が「無い」とだけ返し、何が無いのかを言わない。

## 発見の観点

実ペルソナ 2 名の同一観測（第 8 周）。

## 解決の観点

unavailable の全経路に閉世界の事実文を 1 つ持たせる。

## 一般化

結果の形に「理由」の居場所が無いと、否定の結果は理由を落とす。
