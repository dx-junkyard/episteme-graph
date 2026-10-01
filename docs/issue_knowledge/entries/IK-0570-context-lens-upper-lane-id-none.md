---
id: IK-0570
title: "部品の文脈の上位レーン（中心命題・支持構造）の項目が id=None で上へ辿れない"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-10-01
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [deliberation_w, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: low
    structure: low
    connection: low
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    第 15 周の trace 場面で、上位 6 件が同列の「（中心命題）を支持する」で id=None だった。W層 context_lens の上位レーン導出に由来。 原因の性質で座標を置いた（症状の場所ではなく）。 未解決のため座標は暫定。
generalization:
  level: general
  general_form: "文脈の項目を名前だけで作り、辿り先の参照を持たせない"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: '部品の文脈の上位レーンの中心命題・支持構造の項目は id を持たせず（辿れない項目として正直に出し）、議論の開幕画面で読める出口を事実文 exits で示す。'
  landed_in:
    - backend/core/component_context.py
    - frontend/public/js/app.js
    - backend/tests/test_ik0588_component_context_hops.py
    - e27ee17
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "ブラウザでの画面確認"
      - "ハーネスの画面投影が exits を描かないため、再演の画面上の事実文は未確認（砂場 API の GET で DTO を確かめた）"
related: []
view_of: []
history:
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'open'
    reason: '2026-10-01 第 18 波で是正中（別担当がコードを是正している）。'
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '第 18 波（e27ee17）で是正し、第 18 周で部品文脈の DTO を砂場 API の GET で取り、中心命題の項目に id キーが無いこと・exits の事実文が付くことを見た。（persona_enactment_testing_design.md §18.11）'
---

## 課題

部品の文脈の上位レーン（中心命題・支持構造）の項目が id=None で上へ辿れない。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

解決（e27ee17）。中心命題・支持構造の項目は辿れない項目として id を持たせず、議論の開幕画面への出口を事実文（exits）で示す。第 15 周の観察（上位 6 件が id=None）はこの是正で「辿れない理由が事実文で出る」状態になった。

## 一般化

文脈の項目を名前だけで作り、辿り先の参照を持たせない。
