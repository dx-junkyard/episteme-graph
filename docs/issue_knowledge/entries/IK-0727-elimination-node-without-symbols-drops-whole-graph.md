---
id: IK-0727
title: "消去対象の記号を持たない eliminate step が 1 つあるだけで、理論操作グラフ全体（135 ノード・284 辺）が保存されない"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_autonomous_loop_design.md"
  - "docs/pipeline/overview.md §4"
feature_context:
  realizing: "論文の導出 step を理論操作グラフ（main / 式の詳細 / debug の 3 層）に正規化し、export_validation を通して保存する"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [representation, aggregation]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation = 「消去を宣言したが消去対象が材料に無い step」を表す居場所がグラフの表現に無く、normalizer は
    宣言どおり eliminate のノードを作った。構造軸 aggregation = 縮退の判定単位がグラフ全体で、ノード単位の error でも
    全ノード・全辺を捨てていた。接続軸 contract = validator は eliminate ノードに eliminated_symbols を要求するが、
    derivation_chain の step はそれを保証しない（実データで空）。処理軸 none（validator・gate の判定自体は正しい）。
    統制軸 none（順序・予算・手続の問題ではない）。
generalization:
  level: general
  general_form: "生成側が検証側の必須項目を保証しない宣言をそのまま写し、検証の失敗を受けた縮退が全体単位のため、一要素の欠陥で全体が保存されない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回（2026-10-16・arXiv 2610.20589 TeX 経路）で教材に理論操作グラフが無いことに気付き、component_graph の artifact（135 ノード）と export_validation の error 1 件（elimination node eq_op_0026 has no eliminated_symbols）、theory_component_graphs に行が無いことを突き合わせた。derivation_chain の当該 step は operation=eliminate・eliminated_symbols=[] だった。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: "生成側: normalizer は消去対象の記号が無い eliminate step を記号を推測で補わず generic（transform）に落とし、宣言された操作を review_reason に、理由コード elimination_target_unresolved を review_reasons に残す（式 backing があれば式の詳細に partially_source_backed、#361 と同じ規則）。保存側: export_validation の component_graph error がすべてノード単位で降格により解消できる規則なら、該当ノードを debug / review_required に降格し ComponentGraphValidator で再検証、error がゼロのときだけグラフを保存する（列挙外の error が 1 件でもあれば従来どおりグラフを保存しない）。validator の hard error は変えていない。"
  landed_in:
    - "src/episteme_graph/agents/component_graph/normalizer.py"
    - "src/episteme_graph/agents/component_graph/schema.py"
    - "backend/core/document_pipeline/graph_demotion.py"
    - "backend/core/document_pipeline/orchestrator.py"
    - "src/tests/agents/component_graph/test_unresolved_elimination.py"
    - "backend/tests/test_component_graph_node_demotion.py"
  verification:
    methods: [guardrail, scratch_db]
    unverified:
      - "実機で当該教材を再解析し theory_component_graphs に行が入ること（DB 書き込みを伴うため未実施。artifact を読み出して正規化・降格・再検証を手元で実行し error ゼロを確認した）"
      - "降格ノードを含むグラフの論文層・理論モジュール・グラフレビューでの表示"
related: [IK-0725]
view_of: []
history: []
---

## 課題

症状: 135 ノード / 284 辺の理論操作グラフが生成されたのに、export_validation の hard error 1 件
（`elimination_missing_eliminated_symbols`、ノード `eq_op_0026`）で orchestrator が「graph のみスキップ」し、
`theory_component_graphs` に行が無い。教材は「理論操作グラフが無い」状態になり、論文層・理論モジュール・
レーダーの深い段の材料が全部縮退した。
原因: ①derivation_chain の step が `operation="eliminate"` を宣言しつつ `eliminated_symbols=[]`（本文は
「加速度の二次の項を打ち消す」で、消去対象は記号ではなく項）。normalizer はこれをそのまま eliminate ノードにした。
②縮退の判定単位がグラフ全体で、ノード 1 つの欠陥でも全体を捨てていた。

## 発見の観点

artifact・export_validation の error・保存表の有無を突き合わせた（data_inspection）。

## 解決の観点

消去対象が無い宣言に居場所を作り（generic + 理由コード + 宣言の保持 = representation_change）、gate の縮退を
ノード単位の降格 → 再検証にした。消去対象を入出力式の記号差分から補う案は採らなかった（本件のように消去対象が
記号でない step で誤った記号を「消去した」と書くことになるため）。再発はテストで固定（guardrail_fix）。

## 一般化

生成側が検証側の必須項目を保証しないまま宣言を写すと、検証は正しく落とすが、縮退が全体単位だと一要素の欠陥で
全体が消える。辞書の型 information-dropped-as-unrepresentable（居場所の無い宣言が全体ごと落ちる）。
