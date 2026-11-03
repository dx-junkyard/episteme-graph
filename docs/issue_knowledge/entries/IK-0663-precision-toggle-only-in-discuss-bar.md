---
id: IK-0663
title: "精読モードのトグルが論文との議論の帯の中にだけあり、「順番に学ぶ」側から見つからない"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-10
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/understanding_cycle_design.md"
feature_context:
  realizing: "学習者が精読モードを ON にして、予想を書いてから読む学び方を選ぶ"
  layers: [understanding_cycle, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [condition]
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
    確認: トグルは議論モードの帯（index.html の discuss バー）の中にあり、帯は議論中でないとき隠れる（app.js）。精読モードは opt-in（UC1）なので、入口が見えないと選ぶ機会が無い。構造軸 decomposition = 学び方の設定がモードの帯という別の単位に属している。接続軸 condition = 入口の表示が議論中という条件に結びつく。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "利用者の明示の選択に依存する機能の入口を、別の状態でしか見えない場所に置く"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, invariant_audit]
  note: "第 23 周の視覚ベースラインと机上レビューで、opt-in の入口が議論モードの帯にしか無いことを見た。"
resolution:
  perspective: [single_point_fix]
  note: "精読モードのトグルを議論の帯から教材区画の見出し行（読む・講義・引っかかりと同じ部品）へ移し、「順番に学ぶ」側からも見つかるようにした。状態の正本は localStorage のまま。"
  landed_in:
    - "frontend/public/index.html"
    - "frontend/public/css/styles.css"
    - "frontend/public/js/app.js"
    - "backend/tests/test_understanding_cycle_ui_static.py"
  verification:
    methods: [manual_ui, guardrail]
    unverified:
      - "スマートフォン幅での見出し行の折り返し"
      - "ハーネスからはトグルを押せないため、砂場のペルソナはトグルの位置を確かめていない"
related: [IK-0655, IK-0662]
view_of: []
history: []
---

## 課題

症状: 「順番に学ぶ」で読んでいる学習者は精読モードの入口を見つけられない。原因: トグルが議論中にだけ見える帯の中にある。

## 発見の観点

砂場の実機（reproduction）と、UC1 の opt-in × 入口の置き場の読み合わせ（invariant_audit）。

## 解決の観点

トグルを教材区画の見出し行に置き直した。指揮者が内蔵ブラウザ（幅 1280）で、見出し行に出て議論の帯が折れないことを確かめた（視覚ベースライン V3''）。

## 一般化

明示の選択でしか有効にならない機能は、入口が見えなければ存在しないのと同じ。型は last-mile-missing。確かめ方は、各モードの画面から入口に到達できることを操作列で見ること。
