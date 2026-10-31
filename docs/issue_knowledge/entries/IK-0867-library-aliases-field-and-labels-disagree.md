---
id: IK-0867
title: "ライブラリのエントリ詳細に別名の一覧が 2 つあり食い違う — 上の「別名 (カンマ区切り)」欄は aliases（JSONB）を、下の「別名・隠しラベル」区画はラベル表を描き、ミラーが aliases → ラベル表の片方向なので、区画から足した別名が上の欄に出ず、教員はどちらが正しいか判断できない"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/concept_registry_design.md §4.3"
feature_context:
  realizing: "教員がエントリに別名を足し、そのエントリの別名を確かめる"
  layers: [concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 aggregation: backend/core/library/store.py:136-154 mirror_aliases_to_labels は aliases JSONB を
    kind='alternate' のラベル行へ片方向にミラーし、ラベル表で足した別名を aliases へ書き戻さない（設計どおり）。
    frontend/public/js/admin.js:2554 の「別名 (カンマ区切り)」欄は entry.aliases を、別名・隠しラベル区画は
    /labels を描くので、区画から追加した「Tolman–Oppenheimer–Volkoff 方程式」が上の欄に出ない。読み手の正本はラベル表と
    決めてあるのに、編集面に旧い正本（aliases）が残っている。
generalization:
  level: repo_pattern
  general_form: "正本を新しい表へ移した後も旧い欄を編集面に残し、片方向の同期のため 2 つの一覧が食い違って見える"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction]
  note: >-
    教員 te-01 が別名を足した直後に「上の欄は 2 つのまま。どちらが正しい一覧か迷う」と記した。run:
    uxsim/runs/c-astro-r25-share-r1/20261022T125310Z-41522 seq9-10。
resolution:
  perspective: [canonical_source]
  note: >-
    詳細の編集フォームから「別名 (カンマ区切り)」欄を外し、保存は aliases を送らない（別名の正本はラベル区画。作成時の入力はミラーのまま）。
  landed_in:
    - "frontend/public/js/admin.js"
    - "docs/manual/teacher/19-admin-knowledge-library.md"
    - "backend/tests/test_share_round25_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実機での編集・保存の操作"
related: []
view_of: []
history: []
---

## 課題

症状: 別名を足すと下の区画だけが増え、上の欄は変わらない。原因: 別名の正本がラベル表に移ったのに、編集面に旧い aliases 欄が
残り、同期が片方向。

## 発見の観点

教員ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。編集面を正本 1 つにする。

## 一般化

正本の移行で旧い欄を残すと食い違いが見える型。確かめ方は、区画から別名を足し・見送ったあと詳細を開き直し、別名の一覧が
画面に 1 つしかないことを見ること。
