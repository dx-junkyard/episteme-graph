---
id: IK-0418
title: "帰還の扉（GET .../cycle/return-door）が空のとき {empty: true} だけを返し、何が残っていないのか・どうすれば次回ここに出るのかを言わない（議論の最初に書いた動機が扉に出ないことも言わない）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者がコースに戻ったとき、前回の自分の書き置き・持ち越した問いを見る（理解サイクル・帰還の扉）
  layers: [understanding_cycle]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
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
    構造軸: 空の扉の DTO は empty だけで、何が無いかを置く場所が無かった（representation）。接続軸: 学習者が残したのは開いた動機（opening_motive）だけで、扉の3部品（書き置き・持ち越した問い・確定した引っかかり）には入らない — その区別が応答に届かない（information。medium）。処理軸: build_return_door の分岐は設計どおり（none）。開幕画面に持ち越した問いが出なかったのは、持ち越した問いを一度も残していないためで不具合ではない（has_motive は動機の有無）。確認: 第 8 周の学生が「empty: true とだけ出た。動機を書いたはずなのに出てこない」と記録。
generalization:
  level: repo_pattern
  general_form: 「無い」という結果を返すとき、何が無いのかを結果の形に載せる場所が無く、理由が黙って落ちる
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: 学生ペルソナが翌日に扉を開き、空の応答だけを受け取った。
resolution:
  perspective: [representation_change]
  note: >-
    空の扉を {empty: true, fact, hint} にした（正本 core/cycle/schema.py の EMPTY_DOOR_FACT / EMPTY_DOOR_HINT。何が残っていないか・議論を終えるときに書き置きか持ち越す問いを残すと次回ここに出ること・開いた動機はここに出ないこと）。導出失敗時は {empty: true} のまま（「残っていない」と言い切らない）。画面は RD3（書かなければ何も出ない）どおり空の扉を描かない — この 2 文は API 利用者と将来の表示のための事実で、画面に出すかは別の判断。
  landed_in:
    - backend/core/cycle/derive.py
    - backend/core/cycle/schema.py
    - backend/tests/test_return_door_core.py
    - backend/tests/test_return_door_api.py
    - docs/features/return_door_design.md
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 学習画面（app.js）は空の扉を描かない（RD3。表示するかはフロント担当とオーナーの判断）
related: [IK-0386, IK-0410]
view_of: []
history: []
---

## 課題

空の扉が理由も残し方も言わない。

## 発見の観点

実ペルソナの翌日の再訪（第 8 周）。

## 解決の観点

空の結果に事実文と残し方を持たせる。

## 一般化

否定の結果は、理由の居場所が無いと理由を落とす。
