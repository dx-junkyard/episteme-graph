---
id: IK-0669
title: "持ち越しの候補が 1 つも無いときも「次に持ち越すなら、どの問いにしますか？」の見出しのままで、書かずに閉じてよいことが出ない"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/understanding_cycle_design.md"
feature_context:
  realizing: "疲れた学習者が議論を終え、何も持ち越さずに着地画面を閉じる"
  layers: [understanding_cycle, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: discuss.js の cycleLeaveSectionHtml は候補の有無にかかわらず同じ見出しを出し、候補ゼロのときは「自分の言葉で書く」1 つだけになる。空のときの事実文も、閉じてよいことの一言も無い。処理軸 wording のみ。
generalization:
  level: repo_pattern
  general_form: "選択肢が空のときも選ばせる問いの文面を出し続け、選ばないことが許される事実を言わない"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、夜に何も残さず終えた学生が「選べる問いが出ていないのに聞かれる」「閉じても怒られないなら」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（frontend）: 候補ゼロのときは見出しを事実文（「この会話から選べる問いはありません。書かなくても閉じられます。」等・文言はサーバ）にし、「自分の言葉で書く」は補助に下げる。"
related: [IK-0418]
view_of: []
history: []
---

## 課題

症状: 候補ゼロでも選ばせる見出しのまま。原因: 空のときの文面の分岐が無い。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。空状態の事実文を足すだけの局所の是正。

## 一般化

空の選択肢に選ばせる文面を出すと、利用者は宿題を課されたと読む。確かめ方は、候補ゼロの状態を作って画面の文を読むこと。
