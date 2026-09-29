---
id: IK-0406
title: "記号の照会で available: false のとき、学習画面はサーバの事実文（facts）を捨てて固定文を描き、「登録が無い」「照会できる論文が無い」の区別が画面に届かない"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が式の記号をタップして定義を見る（概念レジストリ P3-5）
  layers: [frontend_learning_ui, concept_registry]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
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
    処理軸: openSymbolLookup の available が偽の分岐が、応答の facts を見ずに固定の1文だけを渡していた（logic）。
    接続軸: サーバは IK-0386 で unavailable の全経路に理由の事実文を1つずつ持たせたが、その情報が画面へ
    運ばれない（information）。available が真の経路は renderSymbolLookupPopover が facts を並べるので、
    分岐の片側だけの欠落。構造・統制は none。
generalization:
  level: repo_pattern
  general_form: 否定の結果にだけ画面側の固定文を当て、サーバが返した理由の事実文を捨てる
pattern: available-but-unwired
discovery:
  perspective: [trace_walk, reproduction]
  note: >-
    第 8 周で学生 2 名が同じ記号を照会し、理由の分からない「見つかりませんでした」を見た件の是正を、
    応答から描画まで辿った。IK-0386 の未確認欄に残っていたフロント側の分岐。
resolution:
  perspective: [carry_through]
  note: >-
    available が偽のとき、応答の facts（文字列の要素だけ）を描く。facts が空のときだけ従来の固定文へ
    縮退する。記号の表記もサーバが返した symbol を優先する。available が真の経路はすでに facts を
    並べていた（「論文『…』の記述です。」などの出所の事実文もそこで出る）ので変更しない。
  landed_in:
    - frontend/public/js/app.js
    - docs/manual/student/02-student.md
    - backend/tests/test_learner_facts_wave6_ui_static.py
  verification:
    methods: [guardrail]
    unverified:
      - ブラウザで登録の無い記号をタップし、サーバの事実文が枠に出ること
      - facts が複数行のときの枠の見え方
related: [IK-0386, IK-0380, IK-0387]
view_of: []
history: []
---

## 課題

記号の照会が「無い」を返したとき、画面がサーバの理由の事実文を捨てて固定文を出す。

## 発見の観点

応答から描画までを辿り、否定の分岐だけが応答の facts を読んでいないことを見た。

## 解決の観点

否定の分岐でもサーバの事実文を後段（画面）まで運び、空のときだけ固定文に縮退する。

## 一般化

否定の結果に画面側の固定文を当てると、サーバが返した理由が落ちる。
