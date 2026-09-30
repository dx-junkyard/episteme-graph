---
id: IK-0556
title: "議論中の論文があるのに、前提説明・痕跡の出典が他の論文のチャンクへ流れていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.8
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat, discuss]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    `_discussed_document_ids` を前提説明の `prefer_document_ids` と痕跡 cited_chunk_ids の document スコープへ渡した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "会話が特定の文書に係留されているのに、下流の検索・記録がその係留を受け取らない"
pattern: scope-widened-silently
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [carry_through]
  note: "`_discussed_document_ids` を前提説明の `prefer_document_ids` と痕跡 cited_chunk_ids の document スコープへ渡した。"
  landed_in:
    - backend/api/routes/learning.py
    - backend/api/services.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 16 波の是正後に同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

議論中の論文があるのに、前提説明・痕跡の出典が他の論文のチャンクへ流れていた。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

`_discussed_document_ids` を前提説明の `prefer_document_ids` と痕跡 cited_chunk_ids の document スコープへ渡した。

## 一般化

会話が特定の文書に係留されているのに、下流の検索・記録がその係留を受け取らない。
