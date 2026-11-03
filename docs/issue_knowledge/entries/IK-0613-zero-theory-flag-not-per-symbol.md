---
id: IK-0613
title: "比較器が理論出力のゼロを出力全体の 1 つの真偽で持つため、出力の 1 つだけがゼロでも全体がゼロであるかのような事実文になる"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "理論の出力が比べられない状態であることを、どの出力についてかを含めて学習者に示す"
  layers: [simulation_bridge_sb, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
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
    確認: 第 21 周の時点の backend/core/simulation_bridge/comparator.py は理論出力がゼロであることを出力全体に 1 つの真偽で持ち、事実文は「理論の出力がゼロのため」と全体の文になった。出力の 1 つだけがゼロの実行で学生が食い違いを指摘した。構造軸 representation = 記号ごとのゼロを表す居場所が無い。接続軸 meaning = 全体の文として読まれる。処理軸 logic = 集約の粒度の誤り。統制軸 none。
generalization:
  level: general
  general_form: "複数の要素についての状態を全体に 1 つの真偽で持ち、一部の要素の状態を全体の状態として告げる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、出力の 1 つだけがゼロの実行で、学生が「ゼロなのはその 1 つだけ」と食い違いを指摘した。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: "理論の出力がゼロの記号を記号ごとに持ち、事実文は記号を名指しする（schema.zero_theory_fact・数は書かない）。他の記号の相対差は従来どおり測る。"
  landed_in:
    - backend/core/simulation_bridge/comparator.py
    - backend/core/simulation_bridge/schema.py
    - backend/tests/test_simulation_bridge_comparator.py
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail]
    unverified:
      - "再演に出力の一部だけがゼロの実行が無く、画面での確認はしていない"
related: [IK-0606]
view_of: []
history: []
---

## 課題

症状: 出力の 1 つだけがゼロの実行で、事実文が理論の出力全体がゼロであるかのように読める。原因: 比較器がゼロを記号ごとではなく全体の 1 ビットで持っていた。

## 発見の観点

ペルソナの通し受講（reproduction）で学生の指摘として現れ、比較器の状態の持ち方を読んだ。

## 解決の観点

ゼロの判定を記号ごとに持ち、事実文はゼロの記号を名指しする形に変えた。ガードレールで確かめた。再演では該当する実行が出なかった。

## 一般化

要素ごとの状態を全体の 1 ビットに畳むと、部分の事実が全体の主張になる。型は information-dropped-as-unrepresentable。確かめ方は、出力の一部だけがゼロの実行を作り、事実文がその記号だけを名指すことを見ること。
