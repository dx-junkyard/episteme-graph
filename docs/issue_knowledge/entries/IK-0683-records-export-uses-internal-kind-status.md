---
id: IK-0683
title: "わたしの記録の持ち出しファイルが種類と状態を内部名（help_usage・superseded 等）だけで持ち、画面の「取り消し」と「書き直しで差し替え」をファイルでは区別できない"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/trace_registry_sovereignty_ledger_design.md"
feature_context:
  realizing: "学習者が自分の記録を持ち出し、画面で見た状態とファイルを突き合わせる"
  layers: [trace_registry, learner_experience_b]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: trace_ledger.build_ledger_export は kind・status を生値で持ち、画面の表示語（kind_label・status_label・理由別の差し替え）を持たない。画面では理由別のラベルで読めた 2 行が、ファイルではどちらも superseded に見える。構造軸 representation = ファイルに表示語と理由の欄が無い。接続軸 meaning = 画面とファイルで同じ行の呼び名が違う。
generalization:
  level: instance
  general_form: "画面とファイルが同じ記録を別の語彙で表し、画面で区別できた状態がファイルでは区別できない"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、懐疑的な学生が持ち出したファイルを開き「取り消しと書き直しで差し替えがどちらも superseded なのか区別がつかない」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core）: 各行に画面と同じ kind_label・status_label を併記し、生値は残す（スキーマは追加のみ）。"
related: [IK-0653, IK-0656]
view_of: []
history: []
---

## 課題

症状: 持ち出しファイルが内部名だけ。原因: ファイルに表示語が無い。

## 発見の観点

ペルソナの通し受講（reproduction）。

追記（第 23 周 st-06）: 書き直しと取り消しをした後に持ち出すと、画面では「取り消し」と「書き直しで差し替え」に分かれていた行が、ファイルでは同じ superseded と help_usage 等の内部名になり、区別がファイルで消える（画面の側は第 23 波で理由別の表示語に分けた — 記録の画面と持ち出しの語が別の正本を読んでいる）。

## 解決の観点

未解決。表示語を併記するだけで、既存キーは変えない。

## 一般化

画面とファイルの語彙が違うと、本人が持ち出した記録を読めない。確かめ方は、画面の各行の表示語がファイルの同じ行にあることを見ること。
