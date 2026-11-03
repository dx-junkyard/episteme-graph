---
id: IK-0622
title: "共有時の対象主張の候補に本文を特定できない主張も載せ、ラベルが内部 ID か「本文を特定できない要素」になるため、学習者が対象を選べない"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "学習者が実行を共有するとき、どの観測系の主張についての結果かを選ぶ"
  layers: [simulation_bridge_sb, doubt_d, frontend_learning_ui]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: backend/api/routes/simulation_lab.py の _targets は backend/core/doubt/observation_targets.py の observation_claim_targets の行を、本文の有無を問わず {claim_id, label} にして返す。本文が引けない主張は label が空か ID 由来になり、表示投影が「本文を特定できない要素」に置き換えるか、置き換えの網をすり抜けた内部 ID がそのまま出る。処理軸 input_handling = 中身の無い候補を落とさない。接続軸 information = 本文が後段へ届かない主張を候補として渡す。構造軸 none。統制軸 none。確定には、観測対象の同定で本文を引けなかった主張の label の値と、内部 ID が投影を通った理由を確かめる。
generalization:
  level: repo_pattern
  general_form: "選択肢の候補に中身の引けない項目を残し、その項目が識別子か「不明」の表示で並ぶため、利用者は何を選んでいるか分からない"
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、共有する場面で、対象の候補に英語の原文・「本文を特定できない要素」・内部の主張 ID が混ざった。"
resolution:
  perspective: [canonical_source, fail_closed]
  note: "観測系の主張の候補から本文の無いもの・「（本文を特定できない要素）」・内部 ID 形のラベルを外す。判定は service.is_readable_target_label / readable_observation_targets の 1 関数で、学習者と教員の両 route が呼ぶ。1 件も残らなければ overview の facts に FACT_NO_OBSERVATION_TARGETS。"
  landed_in:
    - backend/core/simulation_bridge/service.py
    - backend/api/routes/simulation_lab.py
    - backend/api/routes/simulation_bridge.py
    - backend/tests/test_simulation_bridge_service.py
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail, reproduction_rerun, docker_e2e]
    unverified:
      - "残った候補が TeX の崩れた定義文で観測量の主張ではない論文での扱い（再演で見えた）"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "1 箇所の正本と全経路の結線の部品を採った（学習者・教員の 2 route が同じ判定を呼ぶ）。"
related: [IK-0626]
view_of: []
history: []
---

## 課題

症状: 共有時の対象の候補に、本文を特定できない要素や内部の主張 ID のラベルが並ぶ。原因（仮説）: 候補を作るときに本文の無い主張を落とさず、ラベルの代替に ID や不明表示が入る。英語の原文は教材の言語なので課題ではない。

## 発見の観点

ペルソナの通し受講（reproduction）で観測した。

## 解決の観点

読めない候補を外す判定を 1 関数に置き、両 route が通るようにした。再演で「本文を特定できない要素」の羅列は消えた（GONE）。

## 一般化

中身の無い候補を代替表示で埋めると、選択そのものが成り立たない。型は fallback-fabricates-missing-link（代替であることは伝わるが、候補として残すこと自体が問題、という点で最も近い）。確かめ方は、本文の引けない主張を含む教材で共有の候補を出し、ID や不明表示の候補が無いことを見ること。
