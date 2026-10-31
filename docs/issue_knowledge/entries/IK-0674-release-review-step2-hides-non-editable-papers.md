---
id: IK-0674
title: "リリース前の確認のステップ 2 が、編集権限の無い論文の配置を他と同じ見た目で並べ、「次へ」で確認されないことを押す前に言わない"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/release_review_flow_design.md"
  - "docs/features/decision_context_design.md"
feature_context:
  realizing: "閲覧権限だけを持つ論文を含むコースを、教員がリリース前の確認で公開する"
  layers: [release_review, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
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
    確認: GET .../landscape/courses/{id}/placements は documents[].editable を返すが、admin-release-review.js の renderLandscapeBody は描かない。te-02 のコースは配置 8 行がすべて権限の無い論文で、「次へ」は 0 件確認・「対象外」の事実文は押した後のステップ 3 にだけ出た（accept 応答 confirmed: 0）。接続軸 condition = 権限の条件が画面の行まで運ばれない。統制軸 review = 「次へ」で確認される範囲が押す前に読めない。
generalization:
  level: repo_pattern
  general_form: "一括で確定する画面が、確定の対象から外れる行を他の行と同じ見た目で並べ、外れることを確定の後でだけ告げる"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 23 周の砂場で、急ぐ教員が 8 行を見て「次へ」を押し、押した後に「編集権限の無い教材…うちのコースにそんなのあったか」と書いた。accept の応答と decision_context を読んで 0 件確認だったと確かめた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（frontend）: editable=false の論文の見出しに「この論文の配置は所有者が確認します（ここでは確認されません）」を出し、行の操作を無効化して理由を添える。第 3 波の提示集合・不一致の事実文（サーバ）と合わせて使う。"
related: [IK-0376, IK-0659]
view_of: []
history: []
---

## 課題

症状: 閲覧だけの論文の配置が「次へ」で確認されないことが押す前に分からない。原因: 権限の真偽値を画面が描かない。

## 発見の観点

ペルソナの通し受講（reproduction）と accept 応答の読み取り（data_inspection）。

## 解決の観点

未解決。既にある真偽値を行まで配線する。

## 一般化

一括確定の画面で、対象外の行が同じ見た目だと、押した人は全部を確認したと思う。確かめ方は、権限の無い行を含む状態で、押す前の画面に対象外の事実が出ることを見ること。
