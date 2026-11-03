---
id: IK-0688
title: "閲覧権限だけの論文しか無いコースで「次へ」を押すと 0 件確認なのに「表示されていた配置と確認した配置は一致しています」と出る"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-10
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/decision_context_design.md"
  - "docs/features/release_review_flow_design.md"
feature_context:
  realizing: "閲覧権限だけを持つ論文のコースを、教員がリリース前の確認で公開する"
  layers: [decision_context, release_review]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [condition]
    governance: [review]
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
    確認（砂場の版・te-02 run 33000 の accept 応答）: サーバの提示集合は編集できる論文の inferred で組まれ（0 件）、適用も 0 件なので presented_matches_applied=true。画面に出ていた 8 行は client_reported にしか残らない。作業ツリーの第 3 波で提示集合を閲覧できる全論文の inferred にし、不一致の事実文（decision_note）を足した（未再演）。
generalization:
  level: repo_pattern
  general_form: "一致の判定に使う集合を確定の対象側で組むと、画面に出ていたが対象外だったものが判定から消え、何もしなかった確定が一致と言われる"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 23 周の砂場で、急ぐ教員の「次へ」が 0 件確認・一致の事実文になり、accept 応答の decision_context を読んで確かめた。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: "提示集合を「次へ」の画面に出ていた行（閲覧できる全ソース論文の live な inferred）で組み、編集権限が無く適用できなかった行を decision_context の skipped ブロック（id・論文・理由）に別キーで残す。一致しないときだけ事実文 decision_note（RELEASE_DECISION_PARTIAL_NOTE）を返し、1 件も確認しなかった「次へ」も監査に残す。"
  landed_in:
    - "backend/api/routes/landscape.py"
    - "backend/core/decision_context.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/admin-release-review.js"
    - "backend/tests/test_release_review.py"
    - "backend/tests/test_decision_context.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（閲覧のみの教員の「次へ」は、再演時に配置がすべて確認済みで pending が 0 だったため経路に入らなかった）"
      - "画面のステップ 3 に decision_note が描かれる見え方"
related: [IK-0674, IK-0679, IK-0659]
view_of: []
history: []
---

## 課題

症状: 0 件確認で「一致」。原因: 提示集合が確定の対象側で組まれていた。

## 発見の観点

ペルソナの通し受講（reproduction）と応答の読み取り（data_inspection）。

## 解決の観点

提示（閲覧できる集合）と適用（編集できる集合）を別の集合で記録し、差を skipped と事実文で残す（test_view_only_teacher_next_is_not_reported_as_matched）。再演は砂場の状態の都合で経路に入れなかった。

## 一般化

一致の判定を確定の対象側の集合で行うと、見せたものと確定したものの差が判定から消える。確かめ方は、対象外の行だけの状態で押し、不一致として告げられることを見ること。
