---
id: IK-0617
title: "入出力の契約が記号の欄 1 つで「値を入れるキー」と「学習者に見せる表記」を兼ねるため、生の TeX が見出しと入力欄の名前にそのまま出る"
status: open
recorded_at: 2026-10-03
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "生成した理論プログラムの入力欄と出力の表を、学習者が記号で読める形で示す"
  layers: [simulation_bridge_sb, frontend_learning_ui, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [contract]
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
    確認: 生成の入出力契約（io_contract）は symbol の 1 欄だけを持ち、LLM が書いた TeX（例: 添字と書体指定を含む表記）がそのまま入力値の辞書のキーになり、画面（simulation-lab.js / admin-simulation-bridge.js）の見出し・入力欄の名前にも同じ文字列が出る。構造軸 representation = 識別子と表示形の 2 役を 1 欄で兼ねる。接続軸 contract = 表示形を足すには生成側と実行側の両方の契約を変える必要がある。処理軸 none。統制軸 none。
generalization:
  level: general
  general_form: "識別子と表示形を 1 つの欄で兼ね、表示に向く形にすると識別子が壊れ、識別子に向く形にすると表示が読めない"
pattern: id-namespace-conflated
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、全場面で入出力の記号が生の TeX のまま見出しと入力欄に出た。教員場面の共有実行の表も同じだった。"
resolution:
  perspective: [pending]
  note: "部分着地（第 21 波・2026-10-03）: simulation-lab.js が入出力の記号を KaTeX で描き（描けなければ code 要素）、入力欄の name 属性は記号の文字列のまま。core の DTO で照合キーと表示を分ける欄は足していないため open のまま。"
  landed_in:
    - frontend/public/js/simulation-lab.js
    - "docs/features/simulation_bridge_design.md §12.4"
related: [IK-0603]
view_of: []
history: []
---

## 課題

症状: 入力欄の名前と結果表の見出しに生の TeX が出る。原因: 入出力契約の記号の欄が、実行時に値を受け渡すキーと学習者に見せる表記を兼ねている。キーとして使う以上、表示のためだけに書き換えられない。

## 発見の観点

ペルソナの通し受講（reproduction）で観測し、入出力契約の欄を読んだ。

## 解決の観点

部分着地（第 21 波）。表示側で記号を描くようにしたが、照合キーと表示の 1 欄共用（core の DTO）は変えていない。

## 一般化

識別子と表示形は寿命と制約が違う。1 欄で兼ねるとどちらかが壊れる。型は id-namespace-conflated（近縁として）。確かめ方は、添字・書体指定を含む記号で生成し、画面では数式として描かれ、実行のキーは変わらないことを見ること。
