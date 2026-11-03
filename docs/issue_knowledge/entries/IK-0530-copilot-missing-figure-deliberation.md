---
id: IK-0530
title: "Admin Copilot に「図を深く検討する」の capability と手順が無く、教員の問いに道案内できなかった"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/features/admin_assistant_design.md
feature_context:
  realizing: "Copilot で図の検討のやり方を尋ねる"
  layers: [admin_copilot]
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
    capability materials.deliberate_figure と KB 節、intent 5 種を足した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: repo_pattern
  general_form: "画面にある機能が案内の登録簿に無い"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: "教員ペルソナの問いに Copilot が「未整備」を返した。"
resolution:
  perspective: [canonical_source]
  note: "capability materials.deliberate_figure と KB 節、intent 5 種を足した。"
  landed_in:
    - backend/core/admin_assistant/capabilities.py
    - backend/core/admin_assistant/intent.py
    - docs/admin_operations/materials.md
    - backend/tests/test_admin_assistant.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（第 15 周で同じ場面を再運転していない）"
related: []
view_of: []
history: []
---

## 課題

capability が無かった。

## 発見の観点

教員ペルソナの問いに Copilot が「未整備」を返した。

## 解決の観点

capability materials.deliberate_figure と KB 節、intent 5 種を足した。

## 一般化

画面にある機能が案内の登録簿に無い。
