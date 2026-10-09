---
id: IK-0649
title: "リリース前の確認の最後の要約が、「次へ」で確認済みにした配置を「未確認」と言う"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-09
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/release_review_flow_design.md"
  - "docs/features/decision_context_design.md"
feature_context:
  realizing: "教員がリリース前の確認で配置を確認し、公開の直前に受講者へ届くものの要約を読む"
  layers: [release_review, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 配置の確認ステップの「この配置で次へ」は accept を記録した後にステップを進めるだけで配置を取り直さず、要約のステップは確認前に読んだ未確認件数で文を組んでいた（admin-release-review.js の advance と要約の組み立て）。接続軸 version = 要約が確認前の世代の配置を読む。処理軸 none（文の組み立て自体は正しい）。構造軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "書き込みの後に読み手が手元の古い写しを使い続け、書いたばかりの結果と逆のことを利用者に告げる"
pattern: stale-derivative-served
discovery:
  perspective: [invariant_audit, trace_walk]
  note: "第 23 周の机上レビューで、RR2（「次へ」は確認したものとして記録する）と、確認から要約までの画面の状態の受け渡しを読み合わせて見えた。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "accept の後に配置を取り直してから要約を組み、取り直せなかったときは確認前の件数で要約せず事実文を出す。提示と適用に差があったときの出口もウィザード内で配置のステップへ戻る操作にした。"
  landed_in:
    - "frontend/public/js/admin-release-review.js"
    - "backend/tests/test_release_review_ui_static.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場・ブラウザでのステップ遷移と要約の文"
      - "配置の取り直しが失敗したときの表示"
related: [IK-0657, IK-0659]
view_of: []
history: []
---

## 課題

症状: 「この配置で次へ」で inferred を confirmed にしたのに、公開ステップの要約が「うち未確認」として同じ配置を数える。原因: 確認の記録後に画面が配置を取り直さず、要約が確認前の写しを読んでいた。

## 発見の観点

設計書の不変条項（RR2）× コードの読み合わせ（invariant_audit）と、ステップ間の状態の流れを辿った（trace_walk）。

## 解決の観点

派生物（画面の写し）を書き込みの直後に作り直すだけで足りる局所の是正。取り直しに失敗した場合を「古い件数で言う」に戻さないことを静的検査で固定した。

## 一般化

書き込みの成否を返した後の画面は、自分が持つ写しを捨てて読み直さないと、直前の操作と逆の事実を告げる。型は stale-derivative-served。確かめ方は、確認を記録した直後の要約が確認後の状態で組まれることを見ること。
