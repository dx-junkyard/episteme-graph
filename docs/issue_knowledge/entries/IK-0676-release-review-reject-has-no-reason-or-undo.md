---
id: IK-0676
title: "リリース前の確認で却下・再検討した行はボタンが消えてその場で取り消せず、却下の理由を残す欄も無い"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/release_review_flow_design.md"
feature_context:
  realizing: "几帳面な教員が配置の根拠を読んで却下・再検討し、押し間違いを直し、理由を残す"
  layers: [release_review, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [review]
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
    確認: placementRowHtml は status=rejected の行で操作ボタンを描かず、review_required の行にも「戻す」は無い。PATCH は review_note を受けるが画面は送らない。取り消しは脚注で教材管理へ送る。統制軸 review = 否定の判断を直す手段と理由の記録が肯定の判断より弱い。
generalization:
  level: repo_pattern
  general_form: "肯定の判断はその場でやり直せるのに、否定の判断はやり直しも理由の記録もその場でできない"
pattern: negative-decision-second-class
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、几帳面な教員が「却下した行からはボタンが消え、間違えたときに戻す手段がこの画面に無い。却下の理由も残っていない」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（frontend）: 却下・再検討の行に「未確認に戻す」（既存 PATCH）と任意の理由欄（review_note）を置く。"
related: [IK-0660, IK-0657]
view_of: []
history: []
---

## 課題

症状: 却下・再検討をその場で取り消せず、理由も書けない。原因: 否定側の行に操作と記録の入口が無い。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。既存 API を画面から呼ぶだけで足りる。

## 一般化

否定の判断を二級に扱うと、押し間違いが別画面頼みになり、理由が失われる。確かめ方は、却下した直後に同じ画面で戻せて理由が残ることを見ること。
