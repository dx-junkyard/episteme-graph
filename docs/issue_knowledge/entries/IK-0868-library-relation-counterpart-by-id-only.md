---
id: IK-0868
title: "ナレッジライブラリの「関係」の追加は相手を「相手のエントリ ID」の手入力でしか指定できず、エントリの一覧にも詳細にも ID が出ないため、教員は関係を 1 操作で記録できない"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員が概念どうしの関係（上位・関連・同じ・近い）を記録する"
  layers: [concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [assignment]
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
    接続軸 information: frontend/public/js/admin.js:3111 の入力は placeholder「相手のエントリ ID...」の自由テキストで、
    :3152-3159 はそれを object_entry_id として送るだけ。一覧（:2473）・詳細は ID を描かないので、教員が ID を知る経路が
    画面に無い。統制軸 assignment: 関係の記録は人の判断（KR2）なのに、判断する人がその操作に到達できない。
    装置（POST /api/admin/library/relations）は揃っている。
generalization:
  level: repo_pattern
  general_form: "記録の装置は揃っているが、相手を選ぶ入口が内部の識別子の手入力だけで、その識別子を利用者が知る経路が無い"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-03 と te-01 が「相手のエントリ ID を打ち込む形で、ID をどこで調べればいいか書いていない」と記した。
    run: uxsim/runs/c-astro-r25-share-r1/20261022T124804Z-38413 seq3・20261022T125310Z-41522 seq13。
resolution:
  perspective: [explicit_contract]
  note: >-
    関係の相手の欄を名前・別名での検索（GET /entries?q=）+ 候補つき選択にした（名前・種別・分野で並べ、ID は送信値だけ）。見つからないときはサーバの facts（LIBRARY_SEARCH_NOT_FOUND）で新規作成への道を言う。
  landed_in:
    - "frontend/public/js/admin.js"
    - "backend/api/routes/library.py"
    - "docs/manual/teacher/19-admin-knowledge-library.md"
    - "docs/admin_operations/library.md"
    - "backend/tests/test_share_round25_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実機での検索・記録の操作"
related: []
view_of: []
history: []
---

## 課題

症状: 関係を足そうとしても、相手を名前で選べず ID もわからないので記録できない。原因: 相手の入力が ID の手入力だけ。

## 発見の観点

教員ペルソナの通し受講（reproduction）と画面の入力から送信までの追跡（trace_walk）。

## 解決の観点

未解決。既存の検索 API を使った候補つき選択にする。

## 一般化

装置はあるのに入口が内部 ID の手入力の型。確かめ方は、関係を名前だけを頼りに記録し、送られた object_entry_id が
選んだエントリと一致することを見ること。
