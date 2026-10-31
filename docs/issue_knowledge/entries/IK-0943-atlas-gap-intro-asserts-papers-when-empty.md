---
id: IK-0943
title: "「論文の解析から見つかった候補」の区画は候補がゼロでも見出し直下に「複数の論文が、この地図にまだ無い項目に触れています。」と断言し、直後の「いまレビューする候補はありません」と食い違って読める"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/category_gap_candidates_design.md"
feature_context:
  realizing: "教員が論文由来の候補の区画を開き、候補があるか・無いならなぜかを読む"
  layers: [category_gaps, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording: admin.js の GAP_GROUP_INTRO（7270 付近）は区画の説明として常に描かれ、候補の有無で変わらない。
    説明が「〜に触れています」と現在の事実として断言する形なので、候補ゼロ・空の理由（empty_notes）の上に置くと
    矛盾した事実文になる。IK-0851 の是正で空の理由は出るようになったが、見出しの説明は条件付きにしていない。
    構造軸・接続軸・統制軸 none（表示の文の時制・条件の問題）。
    確認: run 20261023T032742Z-99530 seq 3 / 6、run 20261023T070217Z-13812 seq 5。
generalization:
  level: general
  general_form: "区画の説明文を現在の事実の断言で書き、区画が空のときにもそのまま描くため、空の事実文と矛盾する"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "委任型の教員が見送り済みの表示をオンにした画面で、見出しの説明と「候補はありません」の食い違いを記した。 run: uxsim/runs/c-astro-r25-atlas-r3/20261023T032742Z-99530。"
resolution:
  perspective: [single_point_fix]
  note: >-
    区画の説明を定義の文に書き換えた。GAP_GROUP_INTRO =「2 本以上の論文が、この地図にまだ無い同じ項目に触れたとき、ここに候補として出ます。」、EDGE_GROUP_INTRO
    も「…組（プロトタイプ近傍）や…組（共起）が、ここに関係の候補として出ます。」と定義の形にした（出所チップの語の意味も括弧で言う）。
  landed_in:
    - "frontend/public/js/admin.js"
    - "backend/tests/test_atlas_review_round25_r3.py"
    - "backend/tests/test_atlas_gaps_admin_ui_static.py"
  verification:
    methods: [guardrail]
    unverified: []
related: [IK-0851]
view_of: []
history: []
---

## 課題

症状: 候補ゼロの区画に「複数の論文が、この地図にまだ無い項目に触れています。」→「いまレビューする候補はありません。」と
続き、教員は食い違いと読む。

原因: 区画の説明文が条件なしの断言。

## 発見の観点

通し受講（reproduction）。

## 解決の観点

第 3 波（2026-10-23）で是正。区画の説明を定義の文に書き換えた。

## 一般化

空状態の事実文を足した区画では、見出し直下の説明文も空のときに真であるかを確かめる。確かめ方: 候補ゼロで区画を開き、
説明と空の文が両立することを見る。
