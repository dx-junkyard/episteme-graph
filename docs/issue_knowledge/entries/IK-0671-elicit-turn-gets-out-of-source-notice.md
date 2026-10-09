---
id: IK-0671
title: "理解サイクルの「予想を引き出す」往復（答えを配らず問いだけ返す）に「出典が提示できない参考情報」の注意書きが付く"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/understanding_cycle_design.md"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "学習者が予想を書き、AI が答えを伏せたまま条件を問い返す往復を読む"
  layers: [understanding_cycle, rag_chat, frontend_learning_ui]
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
    確認: 注意書き _visible_out_of_source_notice の条件（_no_adopted_grounding・_guard_applies）は casual・締めくくり・CHIT_CHAT を外すが cycle_mode=elicit を外さない。elicit は答えを配らない設計なので出典が無いのは正常で、問い 1 つの往復に「断定ではありません」の警告と「出典を追えない AI の説明」の出所が付く（st-06 run 37568 seq 7）。処理軸 logic = 条件式の漏れ。接続軸 condition = 往復の様式（elicit）が注意書きの判定まで届かない。
generalization:
  level: repo_pattern
  general_form: "ある様式の往復では正常な状態（根拠を出さない）を、別の様式向けの警告の条件がそのまま異常と判定して警告を付ける"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、予想を書いた学生が「問いを返しただけなのに参考情報の警告が付いていて、何が参考情報なのか分からない」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core）: elicit の往復は注意書きの対象から外す（ガードの system 注入は維持するかを判断）。"
related: [IK-0471]
view_of: []
history: []
---

## 課題

症状: 問い 1 つの往復に出典なしの警告が付く。原因: 注意書きの条件が elicit を見ない。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。条件式に様式を 1 つ足す局所の是正。

## 一般化

様式ごとに「正常」が違う往復で、警告の条件が様式を見ないと、正常な往復に警告が付いて警告そのものが読まれなくなる。確かめ方は、各様式の往復で警告が付く条件を表にして実際に送ること。
