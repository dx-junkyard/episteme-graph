---
id: IK-0682
title: "学習チャットで書き直して送った後、元の往復が会話から外れ記録に残ることを画面が言わない（取り消しの後にだけ事実文が出る）"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/trace_registry_sovereignty_ledger_design.md"
feature_context:
  realizing: "学習者が自分の質問を書き直し、元の言い方がどうなったかを確かめる"
  layers: [learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
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
    確認: app.js の deleteMessageFrom は応答の fact（CHAT_TURN_WITHDRAWN_FACT）を appendSystemNote で出すが、書き直しの送信経路（replace_message_id）は事実文を出さない。接続軸 information = 書き直しの結果の事実が画面へ届かない。
generalization:
  level: instance
  general_form: "同じ結果を生む 2 つの操作のうち、片方の操作の後にだけ結果の事実を告げる"
pattern: last-mile-missing
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、懐疑的な学生が「書き直した後、前の質問と回答が消えたのか残っているのか、画面には何も書かれていない」と書いた（取り消しの後は読めた）。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core + frontend）: 書き直しの応答にも同じ事実文（書き直しの語）を載せ、取り消しと同じ関数で 1 行出す。"
related: [IK-0653]
view_of: []
history: []
---

## 課題

症状: 書き直しの後だけ無言。原因: 事実文の配線が取り消しの経路にしか無い。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。既存の事実文の経路を書き直しにも通す。

## 一般化

同じ結果を生む操作の片方だけが結果を告げると、もう片方の利用者は消えたと誤読する。確かめ方は、同じ結果を生む操作をすべて列挙し、それぞれの後に事実文が出ることを見ること。
