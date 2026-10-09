---
id: IK-0668
title: "持ち越しの問いに答えても帰還の扉は答える前と同じで、答えたことも答えの中身も出ない"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/return_door_design.md"
  - "docs/features/understanding_cycle_design.md"
feature_context:
  realizing: "学習者が翌日、持ち越しの問いに答えたあとコースを開き、再訪が閉じたことを確かめる"
  layers: [understanding_cycle, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: build_return_door は書き置き・持ち越し・最後の引っかかりの 3 行だけを入力にし、持ち越しへの答え（intention role=revisit_answer）を読まない。答えた後も同じ持ち越しが扉に出続ける（st-02 run 32810 の seq 11）。接続軸 information = 答えの記録が扉へ届かない。統制軸 completion = 持ち越しが「答えた」状態になる定義が無い。
generalization:
  level: instance
  general_form: "ある問いに答えた記録を残しても、問いを提示する面がその答えを読まないため、答える前と同じ提示を続ける"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、持ち越しの問いに答えて書き直した学生が扉を開き直し「答えたら何か変わると思っていた」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core）: 扉の持ち越しに、答えがあればその逐語（本人の言葉）を添えるか、答えた持ち越しは「答えました」と事実で閉じる（RD1・RD2 は守る — 日数・件数は出さない）。"
related: [IK-0650]
view_of: []
history: []
---

## 課題

症状: 持ち越しの問いに答えても扉は不変。原因: 扉の組み立てが答えの記録を読まない。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。扉の入力に答えの行を足すだけで、再訪の閉じが画面に出る。

## 一般化

提示と応答の記録が別の行に分かれていると、提示する側が応答を読まない限り同じ問いを出し続ける。確かめ方は、答えた直後に提示面を開き直し、答えたことが出ることを見ること。
