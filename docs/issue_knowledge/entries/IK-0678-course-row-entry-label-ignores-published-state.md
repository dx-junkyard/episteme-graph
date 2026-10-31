---
id: IK-0678
title: "コース管理の行の入口が公開済みでも「確認して公開」のままで、ウィザードを通すと元から公開のコースにも「公開しました。」と出る"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/release_review_flow_design.md"
feature_context:
  realizing: "教員が公開済みのコースを見返し、位置づけの確認が残っているかを一覧で読む"
  layers: [release_review, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: admin.js のコース行は visibility に関係なく「確認して公開」を描き（公開状態は別の欄のバッジと「公開する／公開を止める」）、admin-release-review.js の公開ステップは成功時に常に「公開しました。」を出す。統制軸 completion = 公開済み・確認済みの状態が入口の語に映らない。
generalization:
  level: instance
  general_form: "状態が変わっても入口と完了の文言が状態を見ずに同じ語を出し続ける"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、急ぐ教員が「もう public になってるけど、いつもの『確認して公開』を押せばいい」「元から public だったから何が変わったのかは分からない」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（frontend）: 公開済みの行は「位置づけを見直す」に語を変え、元から公開のときは「公開の状態は変わっていません」と事実で返す。"
related: [IK-0659]
view_of: []
history: []
---

## 課題

症状: 公開済みでも入口と完了文が同じ。原因: 文言が状態を見ない。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。状態に応じて語を分ける局所の是正。

## 一般化

状態を見ない文言は、操作の前後で何が変わったかを利用者に読ませない。確かめ方は、公開済みと未公開の両方で入口と完了文を読むこと。
