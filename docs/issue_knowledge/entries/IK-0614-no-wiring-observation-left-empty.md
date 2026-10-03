---
id: IK-0614
title: "配線なしの実行では「模擬観測は理論の出力そのもの」という状態を観測値として表さず空のまま返すため、結果の観測の列が空表示になる"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "配線のない実行でも、理論の出力と観測の関係を学習者に表の形で示す"
  layers: [simulation_bridge_sb, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
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
    確認: 第 21 周の時点の backend/core/simulation_bridge/service.py は配線が無い実行で result.observed を空にし、画面（simulation-lab.js）は平均とばらつきが無い記号を空表示にする。比較器は IK-0606 で未測定を一級の判定にしたが、観測の列の表し方は決まっていなかった。構造軸 representation = 「観測 = 理論値そのもの」の居場所が無い。接続軸 information = 観測の列が空で後段へ渡る。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "比べる相手が無い状態を、相手の値を空にすることで表し、表示側が空を欠落として描く"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、配線なしで実行したペルソナの結果表で、模擬観測の列が空表示だった。"
resolution:
  perspective: [carry_through, guardrail_fix]
  note: "配線なしの run は service.observed_from_theory が result.observed を理論値（mean = 理論値・std なし）で埋め、表と FACT_NO_WIRING の文を一致させた。比較の意味（未測定）は変えない。第 1 波の後の追補で、配線なしの run は記号ごとの相対差も None にし、0.0 を一致と読ませない。"
  landed_in:
    - backend/core/simulation_bridge/service.py
    - backend/core/simulation_bridge/comparator.py
    - backend/tests/test_simulation_bridge_service.py
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail, reproduction_rerun, docker_e2e]
    unverified:
      - "教員画面の計器に相対差のゼロの値が「未測定」と並んで見えた件（後の教員再演・ハーネスの投影か製品の DTO かは切り分けていない）"
related: [IK-0606, IK-0628]
view_of: []
history: []
---

## 課題

症状: 配線なしの実行の結果表で、模擬観測の列が空になる。原因: 配線が無いとき観測は理論の出力そのものだが、その状態を観測値として表さず空のまま返していた。

## 発見の観点

ペルソナの通し受講（reproduction）で観測した。ハーネス側の投影の誤り（IK-0628）と切り分けたうえで、製品側でも空を返していることを確認した。

## 解決の観点

配線なしの run の観測の列を理論値で埋め、表と「理論の出力そのもの」の文を一致させた。記号ごとの相対差も未測定にした。再演で空の列と本文の食い違いは消えた（GONE）。

## 一般化

「相手が無い」を空値で表すと、表示面は欠落として描く。型は information-dropped-as-unrepresentable。確かめ方は、配線なしの実行で結果表の観測の列が値と注記を持つことを見ること。
