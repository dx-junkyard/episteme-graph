---
id: IK-0655
title: "精読モードを ON にしても効くのは動機を記録していない初回の開幕だけで、押した直後も画面が何も変わらない"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-09
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/understanding_cycle_design.md"
feature_context:
  realizing: "学習者が精読モードを ON にして、論文と議論の開幕で予想を書いてから読む"
  layers: [understanding_cycle, discuss, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 精読モードの効果は開幕の動機ブロックの中にだけあり、そのブロックは動機を一度も記録していないときしか描かれなかった（discuss.js の has_motive の分岐）。持ち越しの問いがある開幕や 2 回目以降では予想の導線が出ない。トグルは保存と aria だけで事実文を返さなかった。処理軸 logic = 状態の組み合わせの分岐が網羅されていない。接続軸 condition = モードの状態が動機の有無の分岐を越えて開幕へ届かない。構造軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "利用者が明示に選んだモードが別の条件の分岐の内側でしか読まれず、組み合わせによって黙って効かなくなる"
pattern: condition-not-propagated
discovery:
  perspective: [invariant_audit, trace_walk]
  note: "第 23 周の机上レビューで、マニュアルの約束（ON にすると予想の画面が既定で表示される）と開幕の分岐を読み合わせて見えた。"
resolution:
  perspective: [carry_through, guardrail_fix]
  note: "ON のときは動機の有無・持ち越しの問いの有無に関係なく予想の欄を開いた状態で出し、トグルの直後に状態と次に変わることの事実文を返す。OFF の既定体験（UC1）は変えていない。"
  landed_in:
    - "frontend/public/js/discuss.js"
    - "frontend/public/js/app.js"
    - "frontend/public/index.html"
    - "backend/tests/test_round23_learner_cycle.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場・ブラウザで初回以外の開幕に予想の欄が開くこと"
      - "OFF の既定体験が変わっていないことの画面確認"
related: [IK-0663]
view_of: []
history: []
---

## 課題

症状: 精読モードを ON にしても、2 回目以降・持ち越しの問いがある開幕では予想の導線が出ず、押した直後も変化が見えない。原因: モードの効果が動機ブロックの内側にだけ書かれ、そのブロックが初回にしか描かれなかった。

## 発見の観点

マニュアルの約束 × 開幕の分岐の読み合わせ（invariant_audit）と、状態の組み合わせごとの描画の経路（trace_walk）。

## 解決の観点

モードの状態を開幕の描画全体へ運んだ（carry_through）。組み合わせごとの描画を静的検査で固定した。

## 一般化

明示の選択を別の条件の内側で読むと、組み合わせによって選択が黙って効かなくなる。型は condition-not-propagated。確かめ方は、選択を ON にしたまま他の条件（初回か・持ち越しがあるか）を全通り作り、どの組でも効くことを見ること。
