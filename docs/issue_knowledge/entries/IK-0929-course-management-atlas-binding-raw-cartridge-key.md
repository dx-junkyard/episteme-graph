---
id: IK-0929
title: "コース管理の一覧が地図の割り当てを「地図: astrophysics（0/20 トピック）」と分野の内部キーと件数の比で示し、分野名も「0/20」が何の割合かも読めない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が自分のコース・共有されたコースの地図の割り当てを一覧で確かめる"
  layers: [frontend_admin_ui, field_atlas_s]
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
    接続軸 information: frontend/public/js/admin.js:11768 courseAtlasBindingText は atlas_cartridge_id（内部キー）と atlas_topic_count
    / topic_count を描き、分野名の表示（IK-0866 で library に足した domain_label の解決）を使わない。/api/admin/courses の応答にも分野名が無い。
generalization:
  level: repo_pattern
  general_form: "一覧の 1 行が分野の内部キーと説明の無い比をそのまま描く"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-05 がコース管理で「英語の名前と数字で何を言っているのか分からない」と記した。run: uxsim/runs/c-astro-r25-share-r3/20261023T033729Z-59479 seq2。
resolution:
  perspective: [representation_change]
  note: >-
    /api/admin/courses が atlas_domain_label（atlas_state.domain_display_name・fail-soft）と atlas_binding_coverage_label（label_vocab.atlas_binding_coverage_label の段階の語: すべて / 一部 / なし・比を出さない）を返し、admin.js の courseAtlasBindingText は「地図: 分野名（段階の語）」を素通しで描く。
  landed_in:
    - "backend/api/routes/admin.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_share_round25_r3_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面のコース管理の一覧"
related: [IK-0866, IK-0911]
view_of: []
history: []
---

## 課題

症状: 「地図: astrophysics（0/20 トピック）」と出る。原因: 一覧の行が分野名の解決を通らない。

## 発見の観点

教員ペルソナの通し受講（reproduction）。

## 解決の観点

是正済み（第 3 波）。一覧が分野の表示名と段階の語で描かれる。

## 一般化

表示語の写像を面ごとに直す型の残り。確かめ方は、コース管理の全行に分野の内部キーが出ないことを画面で見ること。
