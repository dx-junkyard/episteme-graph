---
id: IK-0618
title: "「共有する」が何を起こすか（教員に見える・台帳の候補になる）を押す前の画面に書いておらず、確認の文は押した後にしか出ない"
status: resolved
recorded_at: 2026-10-03
resolved_at: 2026-10-03
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
  - docs/features/simulation_bridge_design.md §12.4
feature_context:
  realizing: "学習者が自分の実行を教員に共有するかどうかを、帰結を知ったうえで選ぶ"
  layers: [simulation_bridge_sb, frontend_learning_ui, disclosure_axes]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [condition]
    governance: [review]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: frontend/public/js/simulation-lab.js は共有の帰結を確認ダイアログの文にだけ持ち、パネルに常設の説明を置かない。押す前の判断の材料が画面に無い。構造軸 representation = 共有の帰結（誰に見えるか・台帳の候補になる）を判断点に表す居場所が無い。接続軸 condition = 共有の前提が操作の前に伝わらない。統制軸 review = 開示の宣言が判断の手続に組み込まれていない。処理軸 none。
generalization:
  level: general
  general_form: "開示を伴う操作の帰結を操作の後の確認にだけ書き、利用者が操作を選ぶ時点では帰結を読めない"
pattern: disclosure-not-declared
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、共有の意味を尋ねる場面と共有だけの場面で、押す前に帰結を読めないことが分かった。英語話者のペルソナでは固定文の言語も障害になった（既知の i18n の課題）。"
resolution:
  perspective: [explicit_contract]
  note: "「共有する」の左に常設の事実文（確認ダイアログと同文）を置き、共有の応答の facts（FACT_SHARED_UNMEASURED 等）を素通しで表示する。第 1 波の後の追補で文言を「比較が測れている実行なら候補として記帳・測れていなければ共有だけ」に揃え、押す前と後の文の食い違いを解いた。"
  landed_in:
    - frontend/public/js/simulation-lab.js
    - backend/core/simulation_bridge/schema.py
    - backend/tests/test_simulation_lab_ui_static.py
    - "docs/features/simulation_bridge_design.md §12.4"
  verification:
    methods: [guardrail, reproduction_rerun, docker_e2e]
    unverified:
      - "英語話者への固定文（i18n・既知の構造課題）"
      - "文言の追補の後の画面での再確認"
related: [IK-0626]
view_of: []
history: []
---

## 課題

症状: 学習者が「共有する」を押す前に、それで教員に見えること・台帳の候補になることを知る手段が無い。原因: 帰結の文は確認ダイアログにだけあり、パネルの常設の説明が無い。

## 発見の観点

ペルソナの通し受講（reproduction）で、共有を選ぶ前の画面に帰結の文が無いことを観測した。

## 解決の観点

押す前に帰結を読める常設の事実文を置いた。再演で学生が押す前の文を読んで共有を決めた（GONE）。押す前と後の文の食い違いは再演で見つけ、文言を揃えた。

## 一般化

開示の帰結は判断点に置かないと告知にならない。型は disclosure-not-declared。確かめ方は、操作を押す前の画面だけを読んで、誰に何が渡るかを答えられるかを見ること。
