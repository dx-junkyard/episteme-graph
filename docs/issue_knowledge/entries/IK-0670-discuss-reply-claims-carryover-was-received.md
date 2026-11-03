---
id: IK-0670
title: "論文との議論の AI が「持ち越しの問い、受け取りました」と答えるが、持ち越しは画面の操作でしか残らず何も保存されていない"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/discussion_mode_design.md"
  - "docs/features/understanding_cycle_design.md"
feature_context:
  realizing: "学習者が議論の会話の中で「明日はこれを聞きたい」と書き、持ち越しとして残ったかを確かめる"
  layers: [discuss, understanding_cycle, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [meaning]
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
    仮説: 議論の system prompt に「持ち越し・書き置きは画面の操作でだけ残る（この会話は保存の操作ではない）」という事実が無いため、LLM が依頼を受け取った体で答える。直後の着地画面の候補はゼロのまま（st-05 run 37771 seq 11〜13）。構造軸 responsibility = 保存の担い手（画面の操作）を会話が名乗る。接続軸 meaning = 「受け取りました」が保存の意味で読まれる。prompt に事実を足して同じ発話で再演すれば確定する。
generalization:
  level: repo_pattern
  general_form: "会話の応答が、自分には実行できない操作を実行したかのように告げ、利用者は操作が済んだと読む"
pattern: failure-reported-as-success
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、持ち越す問いを会話に書いた学生が「受け取りました」を信じ、直後の候補ゼロと食い違って諦めかけた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core）: 議論の system prompt に「持ち越しは画面の『次に持ち越す』でだけ残る」を事実として 1 行足し、会話では残したことにしない（コースビルダーの「AI は登録できない」と同じ扱い）。"
related: [IK-0651]
view_of: []
history: []
---

## 課題

症状: AI が持ち越しを受け取ったと答え、画面の候補はゼロ。原因（仮説）: 会話に保存の担い手の事実が渡っていない。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。prompt に担い手の事実を置き、会話は保存を名乗らない。

## 一般化

会話が実行権限の無い操作を引き受けた体で答えると、成功の偽表示と同じ結果になる。確かめ方は、同じ依頼を会話に書き、応答が保存を名乗らず画面の操作を案内することを見ること。
