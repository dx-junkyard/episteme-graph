---
id: IK-1162
title: "観測論文の理論操作グラフに「観測量から推定量へ至る手法の連鎖」が載らず、その main 層ノードはノード対話（deliberation の論理要素）にも解決されないため、教員が手法の根拠をグラフ上で確かめて承認できない"
status: open
recorded_at: 2026-11-03
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/graph_dialogue_review_design.md"
  - "docs/features/element_deliberation_workspace_design.md"
  - "docs/pipeline/theory-graph.md"
feature_context:
  realizing: "教員がグラフレビューで観測論文の手法（DCF 法による磁場強度の推定）の根拠を辿り、主張を承認する"
  layers: [graph_review, deliberation_w, theory_artifacts]
classification:
  axes:
    processing: [unknown]
    structure: [representation]
    connection: [target]
    governance: [unknown]
  axis_confidence:
    processing: low
    structure: medium
    connection: low
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: (a) 構造軸 representation — 導出チェーンの抽出が理論式の導出を表す形で、観測論文の「手法の連鎖（観測量→推定量）」を
    main 層に載せる表現を持たない（theory_claims_live 1285 件・theory_components_live 21 件があっても、全体対話には柱密度とフィラメント幅の比の式と
    normalize しか見えていない）。(b) 接続軸 target — main 層ノードが deliberation の論理要素に解決されず、ノード対話の対象が見つからない
    （POST /api/admin/deliberation/sessions 404）。案内先の全体対話は承認（approve_claim）に繋がらない。処理軸・統制軸は見ていないので unknown。
    確定に要る確認: document 1771872a の theory 成果物に DCF 法の節（chunks index 29〜35）由来の要素があるか、main 層ノードの id が
    deliberation の論理要素の解決でどこで落ちるか。
generalization:
  level: repo_pattern
  general_form: "根拠を辿らせる図が、文書の主要な論証の型の一部（手法の連鎖）を表せず、図の要素から検討・承認の場へも渡れないため、根拠を開けないと承認しない利用者がそこで止まる"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    sim-sandbox E-0006（campaign c-sandbox-p03-dcf・対象版 1d1929d8 ura-dev）の試行 2。Cep B 論文（document 1771872a、TeX 版）のグラフレビューで
    教員ペルソナが「偏光角分散・速度分散・密度から磁場強度に至る流れ」を全体対話に問うと、回答は「グラフからはその流れは確認できない」
    （AI の読み・未確認）。続くノード対話 2 回は「このノードは論理要素として解決できない」で blocked、承認に到達せず。砂場 DB の chunks
    index 29〜35 に DCF 法の節（SF/ACF 解析・乱流セル数 N・36×36 箱の偏光角分散・補正係数）が実在することを確かめた。
    run: uxsim/runs/c-sandbox-p03-dcf/20261103T045922Z-85083/transcript.jsonl・report.md。
resolution:
  perspective: [pending]
  note: >-
    未解決。候補: ①手法の連鎖をグラフに載せる（representation_change）。②ノード対話が不可のノードから本文の該当節へ辿る導線を付ける。
    ③全体対話から主張の承認へ繋ぐ（carry_through）。(a)(b) のどちらが主かの確定が先。
  landed_in: []
related: [IK-1161]
view_of: []
history: []
---

## 課題

症状: 精読査読型の教員（根拠を開けないと承認しない）が、観測論文の手法の根拠をグラフで確かめられず、承認に到達しない。

原因（仮説）: (a) 理論操作グラフの抽出が観測論文の手法の連鎖を main 層に載せない。本文には DCF 法の節があるが、全体対話はグラフから
「その流れは確認できない」と答える。(b) main 層ノードが deliberation の論理要素に解決されず、ノード対話が 404 になる。案内先の全体対話は承認に繋がらない。

## 発見の観点

ペルソナの通しで全体対話とノード対話が止まった（reproduction）。砂場 DB のチャンクを読み、手法の節が本文に実在することを確かめた（data_inspection）。

## 解決の観点

未解決。何が分かれば解けるか: theory 成果物に DCF 法の節由来の要素が抽出されているか（無ければ抽出の表現、あれば投影）と、
ノード id から論理要素への解決がどこで落ちるか。候補は手法の連鎖をグラフに載せる・解決できないノードから本文の節へ辿る導線・全体対話から承認へ繋ぐ。

## 一般化

理論式の導出を前提にした図は、観測・実験論文の手法中心の論証で再発しうる。確かめ方: 手法の節が主な論文でグラフを開き、
手法の流れを問う全体対話と、各ノードのノード対話・承認が通るかを見る。
