---
id: IK-0673
title: "論文との議論で、論文名を省いて同じことを聞き直すと、前の往復で出典付きで答えた記述が「抜粋では確認できない」になる"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/focus_document_design.md"
  - "docs/features/discussion_mode_design.md"
feature_context:
  realizing: "学習者が議論の 2 回目（別のセッション）に、前回話した論文について続きを聞く"
  layers: [discuss, rag_chat]
classification:
  axes:
    processing: [unknown]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 前回は「音速の論文について」と明示して答えが出典 10・11 を引いたが、別セッションで論文名を省くと焦点論文が決まらず（4 本並び・③段は直前の引用だけ）、検索が別の論文の抜粋に寄って「確認できない」になる（st-05 run 33722 と 37771）。焦点の解決の記録（focus source）を両往復で比べれば確定する。接続軸 target = どの論文の話かが往復をまたいで運ばれない。処理軸 unknown。
generalization:
  level: repo_pattern
  general_form: "会話の対象が前の往復でだけ決まっていて、次の往復に運ばれないため、同じ問いの答えの根拠が往復ごとに揺れる"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、夜の議論を翌日続けた学生が「さっきは論文から答えてくれたのに、今度は抜粋では確認できなかった。どっちを信じればいいのか」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。焦点の解決記録を比べて原因を確定してから、前回の議論で選ばれた論文を次の往復の焦点の候補に運ぶか、決まらないときは論文を選ぶ部品（IK-0600）を出す。"
related: [IK-0580, IK-0600]
view_of: []
history: []
---

## 課題

症状: 同じ趣旨の問いの根拠が往復で揺れる。原因（仮説）: 焦点論文が往復をまたいで運ばれない。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。焦点の解決の記録で原因を確定してから直す。

## 一般化

会話の対象を往復ごとに解き直すと、言い回しで答えの根拠が変わる。確かめ方は、対象を明示した往復と省いた往復で、焦点の解決結果と引用元を比べること。
