---
id: IK-0910
title: "教材図スタジオで図を採用して保存しても、原稿スタジオの根拠リンク（右ペイン・中央の図の欄）には新しい教材図が出ない — サーバは採用時に linked_figure_ids と evidence_links を登録済みだが、画面の evidence_items はコースを読み直すまで古いまま"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/teaching_figure_studio_design.md"
feature_context:
  realizing: "教員が教材図を採用して保存し、その図が学習者に届く根拠として数えられたかを確かめる"
  layers: [teaching_figures, lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
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
    接続軸 version: admin-lecture-studio.js の lsApplyFigureInsert（2986〜3015 行）は採用結果の evidence_link と linked_figure_ids をtopic に足すが、根拠リンクの描画が優先して読む topic.evidence_items（course-structure がサーバで組み立てた DTO）は更新しない。保存の then も lsRenderCourseStructure() を呼ぶだけで course-structure を取り直さない。再読込後には出る（te-02 seq10 で確認）。
generalization:
  level: general
  general_form: "サーバが組み立てた派生の一覧を画面が持ち続け、同じ画面の操作で元の値を変えても派生の一覧を取り直さない"
pattern: stale-derivative-served
discovery:
  perspective: [reproduction]
  note: >-
    te-01 が t0 に教材図を採用・保存した直後、根拠リンクの図の欄に新しい図が無かった（uxsim/runs/c-astro-r25-studio-r2/20261023T020312Z-65722 seq20）。te-02 が同じコースを開き直すと出た（uxsim/runs/c-astro-r25-studio-r2/20261023T021041Z-70961 seq10）。
resolution:
  perspective: [carry_through]
  note: "採用（onInsert）と保存の成功の直後に lsRefreshTopicServerFacts が course-structure を取り直し、当該トピックのサーバ派生（evidence_items・evidence_state・appendix_state・evidence_links 等）だけを写す。採用直後は編集中の本文に触れず、根拠のチップと右ペインだけを描き直す。"
  landed_in:
    - "frontend/public/js/admin-lecture-studio.js"
  verification:
    methods: [guardrail]
    unverified:
      - "JS は node の無い環境で評価していない（静的検査のみ）"
      - "採用直後に根拠リンクへ図が出ることは実画面で見ていない（第 3 周 te-03）"
related: [IK-0875]
view_of: []
history: []
---

## 課題

症状: 採用・保存したのに、根拠リンクの図の欄に新しい図が出ず、届く対象に数えられたかが分からない。

原因: 画面の根拠一覧はサーバの派生 DTO で、採用・保存の後に取り直さない。

## 解決の観点

解決（2026-10-23・第 25 周 面 D 第 2 波）: 採用（onInsert）と保存の成功の直後に lsRefreshTopicServerFacts が course-structure を取り直し、当該トピックのサーバ派生（evidence_items・evidence_state・appendix_state・evidence_links 等）だけを写す。採用直後は編集中の本文に触れず、根拠のチップと右ペインだけを描き直す。

当初の是正案（記録）:

- 採用・保存の成功後に当該トピックの course-structure（または evidence_items だけ）を取り直す。
- 取り直さないなら、採用直後に「保存後に根拠リンクへ反映されます」の事実文を出す（推奨は取り直し）。

## 発見の観点

第 25 周 面 D 第 2 周（原稿スタジオ → 学生の受講）の通し受講で、ペルソナの観察と HTTP の生応答・製品コードを突き合わせた。

## 一般化

同じ画面で元の値を変えたら、その派生の一覧を取り直す。確かめ方は、図を採用・保存した直後に根拠リンクの図の欄に新しい図があることを見ること。
