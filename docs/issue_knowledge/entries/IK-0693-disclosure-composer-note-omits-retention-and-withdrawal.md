---
id: IK-0693
title: "学習チャットの入力欄の上の開示の 1 行が外部の AI への送信だけを言い、発言がどこに残り、いつ消せるか（保持・取り消し）を言わず、宣言の全文への道も無い"
status: open
recorded_at: 2026-10-10
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/disclosure_axes_design.md"
feature_context:
  realizing: "学習者が質問を送る前に、自分の発言が誰に見え、どこに残り、いつ消せるかを確かめる"
  layers: [disclosure_axes, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
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
    確認: core/disclosure_axes.py の learning_chat の宣言は、対話の画面に置く 1 行（note）が外部送信と成績評価
    だけを言い、取り消し・保持は withdrawal / retention の欄にある。disclosure-note.js は note だけを描き、宣言の
    全文（GET /api/disclosure/learning_chat・わたしの記録の先頭）へ導く手段を持たない。構造軸 representation =
    画面に出す宣言が 1 本の文字列で、どの軸をそこへ出すかを表す形が無い。接続軸 information = 宣言にある保持と
    取り消しの事実が入力欄の段まで届かない。処理軸 none（宣言の文そのものは現物どおり）。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "宣言の一部の軸だけを操作の場所に出し、残りの軸と全文への道を置かないため、利用者が知りたい軸がその場で確かめられない"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、開示を確かめたい学生が使い方の質問で保持と取り消しを確かめた（HELP の答えは正直だった）が、入力欄の上の 1 行には送信のことしか無いと書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core + frontend）: 1 行の末尾に「発言の残り方と取り消し方は『わたしの記録』の先頭で確かめられます」の固定文と移動の手段を足す（宣言の文は DA1 のまま・同意の操作は作らない = DA5）。1 行に保持・取り消しを詰め込むかは、入力欄の高さの規律（1 画面に収める）と比べて決める。"
related: [IK-0654]
view_of: []
history: []
---

## 課題

症状: 入力欄の上の開示の文に、発言の保持と取り消しが無く、全文へも行けない。原因: 画面に出すのが宣言の note 1 本だけで、他の軸への道が配線されていない。

## 発見の観点

ペルソナの通し受講（reproduction）。HELP の答えと入力欄の上の 1 行を並べて見えた。

## 解決の観点

未解決。宣言の全文は既にあるので、そこへの道を 1 行に添えれば足りる見込み。

## 一般化

宣言を持っていても、操作の場所から辿れなければ宣言していないのと同じに読まれる。確かめ方は、入力欄から 1 操作で、保持と取り消しの宣言の文にたどり着けることを見ること。
