---
id: IK-0636
title: "関係式（制約）だけのまとまりは定める記号が空になり、学習者には入出力が逆か欠落しているように読める"
status: open
recorded_at: 2026-10-04
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "学習者が理論モジュールの受け取る記号・定める記号を読み、どの記号を求めるまとまりかを判断する"
  layers: [simulation_bridge_sb, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
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
    確認: 定める記号は式の defined_symbols から導くが、関係式は何も定義しないので空になる。どの記号を求めるかは式の形ではなく生成時に決まる。構造軸 representation = 関係式の「向き」を持つ居場所が無い。接続軸 meaning = 空が「出力が無い」と読まれる。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "向きを持たない関係を入力と出力の対で表示し、出力の欄が空になって利用者が向きを取り違える"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 22 周の学生場面 (i)(a') で、ある記号を求める式のまとまりが「その記号を受け取り何も定めない」と読まれた。"
resolution:
  perspective: [pending]
  note: "部分着地: FACT_NO_OUTPUT_SYMBOLS の文言に「どの記号を求めるかはコードの生成時に決まる」を入れ、学習画面にも出した。式の形（左辺の単一記号・比の形）から向きを推定する案は「推測で埋めない」で見送り。向きを人が指定するか、生成後の入出力を一覧に戻すかが分かれば解ける。"
  landed_in:
    - "backend/core/simulation_bridge/schema.py"
    - "frontend/public/js/simulation-lab.js"
related: [IK-0608]
view_of: []
history: []
---

## 課題

症状: ある記号を求める関係式のまとまりで、定める記号が空になり、その記号が受け取る側に並ぶ。原因: 関係式は何も定義せず、求める記号は生成時に決まるため、式の材料からは向きが出ない。

## 発見の観点

ペルソナの通し受講（reproduction）で、入出力を逆に読んだ場面から見えた。

## 解決の観点

未解決。文言で生成時に決まることを伝えるに留めた。式の形からの推定は分野語や形の推測になるため見送った。

## 一般化

関係を対で表示する表現は、向きの無い関係で空の欄を作る。型は information-dropped-as-unrepresentable。確かめ方は、関係式だけのまとまりで一覧を出し、空の欄の意味が画面で説明されることを見ること。
