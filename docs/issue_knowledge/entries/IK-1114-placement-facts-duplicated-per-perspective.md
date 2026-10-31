---
id: IK-1114
title: "学習チャットの画面文脈で「論文『…』は、分野の地図（版 2026.6）の「中性子星」に置かれています（AIによる推定（未確認））」が 2 行重複する（同じ論文・同じ場所の配置が観点ごとに別の行で、文にすると同じになる）"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "学習者が見ている論文が分野の地図のどこに置かれているかを、AI に重複なく渡す"
  layers: [screen_adapter_sa, rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    core/assistant_context/resolvers/learning.py resolve_placement は配置の行ごとに事実文を作る。landscape_placements
    は観点（perspective）ごとに行を持つので、同じ論文・同じ場所の 2 観点が同じ文の 2 行になる。文に観点を出さない以上、事実の単位は（論文, 場所, 出所）。
generalization:
  level: repo_pattern
  general_form: "行の単位（観点つきの配置）と、文にしたときの事実の単位（観点を言わない）が違い、同じ文が並ぶ"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: >-
    製品頭脳 A 4286（BRAIN_NOTES_product_A_r9.md）: req-04286 の画面文脈で配置の行が 2 回出ていた。
resolution:
  perspective: [single_point_fix]
  note: >-
    resolve_placement で同じ文を 2 度足さない（文の重複を除く）。観点を文に出す変更はしない。
  landed_in:
    - "backend/core/assistant_context/resolvers/learning.py"
    - "backend/tests/test_round25_wave9_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "4286 の往復の再演で 1 行になること（第 10 周）"
related: [IK-1031]
view_of: []
history: []
---

## 課題

同じ事実の 2 行。

## 発見の観点

製品頭脳 A 4286（BRAIN_NOTES_product_A_r9.md）: req-04286 の画面文脈で配置の行が 2 回出ていた。

## 解決の観点

resolve_placement で同じ文を 2 度足さない（文の重複を除く）。観点を文に出す変更はしない。

## 一般化

行の単位（観点つきの配置）と、文にしたときの事実の単位（観点を言わない）が違い、同じ文が並ぶ。型 unit-of-work-undefined。
