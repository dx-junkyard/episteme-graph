---
id: IK-1034
title: "トピックの根拠の一覧（原稿スタジオの中央のチップ・右ペイン・学習者の evidence_items）に「出典: トピック概要」が同じ本文で 2 回並ぶ — 埋め込みの別名 source:topic_summary と source:summary の両方を解決できるように項目を 2 つ作り、それをそのまま一覧に出す"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員が原稿スタジオでトピックの根拠の一覧を読み、何に支えられているかを確かめる"
  layers: [lecture_studio, course_builder, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: backend/core/course_content_builder.py::build_topic_evidence_items の「5) トピック概要」が
    for sid in ("topic_summary", "summary") で同じ概要の項目を 2 つ作る。埋め込みの解決のための別名（参照の索引）と、人に見せる一覧の項目が
    同じリストで表されているので、一覧が別名の数だけ重複する。dedup は kind:id で行うため別名は畳まれない。処理軸・接続軸・統制軸 none
    （値は正しく、見せ方の単位の取り違え）。
generalization:
  level: repo_pattern
  general_form: "参照を解決するための別名の索引と、人に見せる項目の一覧を同じリストで持ち、別名の数だけ同じ項目が一覧に並ぶ"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    共有教員 te-04 が t0 の根拠リンクで「『トピック概要』が同じ英文で 2 回出ている」と記した。読み取りの API で t0 の evidence_items の source が
    excerpt / topic_summary / summary の 3 項目（後の 2 つは同題・同文）であることを確かめた。学習者の教材の evidence_items も同じ。
    run: uxsim/runs/c-astro-r25-studio-r6/20261027T122747Z-6889 seq4・5。
resolution:
  perspective: [canonical_source, representation_change]
  note: >-
    course_content_builder.build_topic_evidence_items は source:topic_summary を 1 項目にし、別名 summary は alt_ids（解決の索引）に
    持たせる。埋め込みの解決（_evidence_item_keys・app.js の lookupEvidenceItem・原稿スタジオの lsEvidenceItemByRef）は alt_ids でも
    引くので、![[source:summary]] の埋め込みは従来どおり解決する。一覧に並ぶ項目は 1 つ。
  landed_in:
    - "backend/core/course_content_builder.py"
    - "frontend/public/js/app.js"
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/tests/test_lecture_studio_round25_r6.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "一覧の項目は 1 つ・別名は解決の索引、を学習画面と原稿スタジオの両方の解決関数が同じ alt_ids で引く。"
  verification:
    methods: [guardrail]
    unverified:
      - "build_topic_evidence_items は読み時に呼ばれる（教材・講義・原稿スタジオ）ので既存コースにも効く想定だが、固定コースの t0 で 1 項目になるかは実機で見ていない"
      - "原稿スタジオの右ペインで 1 項目に見えるかは実機で見ていない"
related: [IK-0455, IK-0875, IK-1010]
view_of: []
history: []
---

## 課題

症状: 根拠の一覧に同じ「トピック概要」が 2 回並ぶ。原因: 埋め込みの別名ごとに項目を作り、一覧がそれを畳まない。

## 発見の観点

教員の通し受講（reproduction）の指摘を、evidence_items の生値で確かめた（data_inspection）。

## 解決の観点

第 6 波（2026-10-27）で是正。解決の索引と表示の一覧を分けた（別名は alt_ids の索引だけ・一覧は 1 項目）。

## 一般化

別名の索引と表示の一覧を混ぜる型（duplicate-canonical-sources）。確かめ方: 概要のあるトピックの根拠の一覧で「トピック概要」が 1 回だけ出ることを見る。

第 7 周の再演（2026-10-28）: **GONE**。根拠ありの t0 の根拠の一覧で「出典: トピック概要」が 1 回だけ（第 6 周は 2 回）。run: uxsim/runs/c-astro-r25-studio-r7/20261027T171857Z-53968 seq4・5。
