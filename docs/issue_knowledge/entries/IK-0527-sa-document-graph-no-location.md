---
id: IK-0527
title: "グラフ全体対話の画面文脈で、主ノードが論文のどの章・式に対応するかが渡されず、AI が所在を答えられなかった"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "グラフ全体について AI に論文との対応を尋ねる"
  layers: [screen_adapter_sa, graph_review]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    document_graph 解決器に主ノードごとの章・式の所在（無いときは FACT_MAIN_NODE_NO_LOCATION）を足した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "解決器が既存の射影の一部だけを事実文にし、問われる情報を渡さない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "mailbox の製品 prompt に章・式の所在が無かった。"
resolution:
  perspective: [carry_through]
  note: "document_graph 解決器に主ノードごとの章・式の所在（無いときは FACT_MAIN_NODE_NO_LOCATION）を足した。"
  landed_in:
    - backend/core/assistant_context/resolvers/graph_review.py
    - backend/tests/test_assistant_context_core.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

所在が grounding に無かった。

## 発見の観点

mailbox の製品 prompt に章・式の所在が無かった。

## 解決の観点

document_graph 解決器に主ノードごとの章・式の所在（無いときは FACT_MAIN_NODE_NO_LOCATION）を足した。

## 一般化

解決器が既存の射影の一部だけを事実文にし、問われる情報を渡さない。
