---
id: IK-0584
title: "教材の出典（source）埋め込みで title に summary（逐語引用）をそのまま入れるため、出典カードに同じ英文が二度並ぶ"
status: resolved
recorded_at: 2026-10-01
resolved_at: 2026-10-01
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.9
feature_context:
  realizing: "ペルソナ通し受講 第 17 周（4 つの根の是正後の再演・c-astro-structure-30）の場面"
  layers: [learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [representation]
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
    `course_content_builder.build_topic_evidence_items` の evidence_links 分岐で、source / claim は `title = summary or raw_id` とし、`app.js` の出典カードは見出し（title）と本文（summary）を両方描く。component は Phase 1 で「title に summary を流用しない」是正済みだが source に同じ規則が無い（構造軸 representation = 見出しの欄に本文を入れている）。接続・統制の問題ではない。
generalization:
  level: repo_pattern
  general_form: "表示用の見出しの欄が空のとき本文で埋め、見出しと本文を並べる描画で同じ文が二重になる"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: "第 17 周の頭脳メモ（〔出典: X〕X の二重表示）を、ハーネスの投影・製品のカード描画・evidence item の組み立ての順に辿った。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: '出典（source）の埋め込みで title に引用文を入れず、見出しは掲載節の見出しだけにした。見出しが無い・引用と同じ文なら title を持たせず、カードは引用だけを描く（読み時適用・freeze 済みの旧教材にも効く）。'
  landed_in:
    - backend/core/course_content_builder.py
    - frontend/public/js/app.js
    - backend/tests/test_ik0584_source_evidence_title.py
    - e27ee17
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "ブラウザでの画面確認（ハーネスの投影は見出しが無いとき id を描くため、再演では見出し欄に ev_ 番号が見えた — ハーネス側の写し漏れ）"
related: [IK-0464]
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
    reason: '第 18 波（e27ee17）で是正し、第 18 周の再演（st-01 / st-02 の教材）で出典カードの英文が見出しと本文に二重に出なくなったことを見た。（persona_enactment_testing_design.md §18.11）'
---

## 課題

教材本文の出典カードが「原文の一文（見出し）＋同じ一文（本文）」で描かれ、日本語の教材の中に同じ英文が二度続く。component では同型の重複を是正済みで、source に規則が及んでいない。

## 発見の観点

第 17 周の頭脳メモ（〔出典: X〕X の二重表示）を、ハーネスの投影・製品のカード描画・evidence item の組み立ての順に辿った。

## 解決の観点

解決（e27ee17）。見出しは掲載節の見出しだけにし、無ければ title を持たせない読み時の是正にした（freeze 済み教材を作り直さずに効く）。カード側は title が無ければ見出しを描かない（id へフォールバックしない）。

## 一般化

表示用の見出しの欄が空のとき本文で埋め、見出しと本文を並べる描画で同じ文が二重になる。
