---
id: IK-0609
title: "印字番号の無い式の表示ラベルが、式本文や定義記号より先に解析が生成した英語の要約を取るため、学習者に英語の断片が並ぶ"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "理論モジュールの入力・出力・前提として、式を学習者と教員が読めるラベルで示す"
  layers: [graph_paper_layer, simulation_bridge_sb, frontend_learning_ui, frontend_admin_ui]
classification:
  axes:
    processing: [logic, wording]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 第 21 周の時点の backend/core/graph_paper_layer/schema.py の equation_display_label は印字番号が無い式で平文（A層の英語要約）を先に取り、TeX 経路で式本文を持つ式でも英語の断片を 40 字で切ったラベルを返していた。処理軸 logic = 候補の優先順の誤り、wording = 利用者の言語と異なる文が出る。接続軸 meaning = A層の要約は内部向けの説明で表示ラベルの意味ではない。構造軸は medium で none（本文・記号の欄は既にある）。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "表示ラベルの候補順で、利用者向けでない生成の要約を対象そのものの表記より先に取り、利用者の言語と異なる断片が一覧に並ぶ"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、全場面で入力・出力・前提・循環の注記に英語の断片が出た。同じ関数をグラフレビューと論文層も通るため、教員場面でも同じ断片が出た。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "式の表示ラベルの正本を論文層 graph_paper_layer/schema.py::equation_display_label 1 つに置き（印字番号があれば番号、無ければ式本文の先頭。A層の英語要約の先頭を使わない）、theory_modules/schema.py の同名関数は委譲だけにした。シミュレーション層の一覧・グラフレビュー・論文層が同じ関数を通る。"
  landed_in:
    - backend/core/graph_paper_layer/schema.py
    - backend/core/theory_modules/schema.py
    - backend/tests/test_graph_paper_layer_core.py
    - backend/tests/test_theory_module_core.py
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail, reproduction_rerun, docker_e2e]
    unverified:
      - "TeX のラベル名を印字番号として出す式（式 (eq:mod_dcf) 形）と「番号なし:」の接頭辞は再演でも残った"
      - "番号の無い長い式の先頭の切り詰めが TeX の途中で切れる表示（再演で新たに見えた）"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "1 箇所の正本と旧実装の委譲の部品を採った。グラフレビュー・論文層・シミュレーション層の 3 面が同じ関数を通る。"
related: [IK-0610]
view_of: []
history: []
---

## 課題

症状: 入力・出力・前提の欄に「番号なし:」に続く英語の生成文が途中で切れて並ぶ。原因: 印字番号の無い式のラベルで、式本文の断片や定義記号より先に解析の英語要約を使っていた。表示の正本の関数を学習者・教員の複数の面が共有しているため、全面で同じ症状になる。

## 発見の観点

ペルソナの通し受講（reproduction）で全場面に同じ断片が出たことから、面ごとではなく共有の表示関数を疑った。

## 解決の観点

表示ラベルの正本を論文層の 1 関数に置き、理論モジュール層の同名関数は委譲だけにした。印字番号が無い式は英語の生成要約ではなく式本文の先頭で示す。再演で英語の断片のラベルは消えた（GONE）。TeX のラベル名の素通しと切り詰めの途中切れは残る。

## 一般化

生成の要約は内部の説明であり、対象の名前ではない。名前の候補順に混ぜると言語・粒度の違う断片が利用者に出る。型は wording-mismatch。確かめ方は、印字番号の無い式だけを持つ TeX 経路の教材で一覧を出し、英語の要約が出ないことを見ること。
