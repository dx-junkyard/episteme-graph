---
id: IK-1054
title: "原稿スタジオの「付録が古いトピック」の知らせが、15 件のトピック題名を 1 つの括弧の中に「 / 」で連結して出し、題名自体の括弧と入れ子になってどのトピックが対象か読み分けられない"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "所有者が付録の古いトピックを知り、まとめて付け直すか個別に直すかを決める"
  layers: [lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    frontend/public/js/admin-lecture-studio.js の appendixStaleHtml が appendix_stale_topics の title を join(" / ") して 1 組の全角括弧に入れる
    （第 6 波・オーナー判断 1 で新設）。題名は「…（問題設定と観測データ）」のように括弧を含むため、括弧が入れ子になる。処理軸 wording / 構造軸
    representation: 対象の集合（リスト）を文の中の挿入句として表現している。接続・統制は関係しない（API の appendix_stale_topics は id・title の
    配列で正しい）。
generalization:
  level: repo_pattern
  general_form: "操作の対象の集合を、項目として並べずに 1 文の挿入句へ連結して見せ、対象を読み分けられない"
pattern: wording-mismatch
discovery:
  perspective: [symptom_report]
  note: >-
    所有者 te-01 が「括弧の中にトピック名がずらっと並んでいてどれが対象か読みにくい」と記した（C 所見）。共有教員 te-05 の画面も同じ。
    run: uxsim/runs/c-astro-r25-studio-r7/20261027T171357Z-51101 seq4、uxsim/runs/c-astro-r25-studio-r7/20261027T171857Z-53968 seq3。
resolution:
  perspective: [representation_change]
  note: >-
    第 25 周 第 7 波（2026-10-28）。知らせ `APPENDIX_STALE_COURSE_NOTICE` を「付録が古いトピックがあります（下の一覧）。…」にし、
    題名は文の括弧に連結せず、折りたたみ（`<details>`・見出しはサーバの `appendix_stale_list_summary`・アンカー
    `lecture-studio.appendix-stale-list`）に 1 行 1 題名・左のトピック一覧と同じ「章.トピック」の番号で並べる。各項目は左の一覧の
    該当トピックを開く。件数は出さない。ハーネスの投影は `sync_keys_wave7.md` の面 D 節に追随の要点を残した。
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "backend/tests/test_lecture_studio_round25_r7.py"
  verification:
    methods: [guardrail]
    unverified:
      - "15 題名の固定コースで一覧が読めるか・項目からトピックが開くかは実機で見ていない（第 8 周 te-01）"
      - "付け直しの結果の知らせ（APPENDIX_REATTACH_ALL_DONE_NOTICE）は題名を「 / 」で連結したまま（同じ型の残り）"
related: [IK-0872]
view_of: []
history: []
---

## 課題

症状: 付録の古いトピックが 1 つの括弧に連結され、題名の括弧と入れ子になって読みにくい。

原因: 対象の集合を挿入句として 1 行に描いている。

## 発見の観点

ペルソナの観察（symptom_report）。

## 解決の観点

第 25 周 第 7 波で是正（2026-10-28）。知らせは「下の一覧」を指し、題名は折りたたみの一覧に 1 行 1 題名（章.トピックの番号・項目から開ける）。

## 一般化

操作の対象が複数あるときは、文に埋め込まず項目として並べる。確かめ方: 付録の古いトピックが多いコースで、各題名が 1 行ずつ読めることを見る。

第 8 周の再演（2026-10-28）: **GONE**。コース見出しの知らせは題名を括弧に連結せず、折りたたみ「付録が古いトピックの一覧」に 1 行 1 題名（te-01・te-05 とも 15 件）。軽微: 一覧の番号は「章.トピック」（2.1）で左の一覧は通し番号（6）のため突き合わせに一手間（頭脳の記録・記帳は見送り）。run: uxsim/runs/c-astro-r25-studio-r8/20261027T223243Z-92722 seq4、uxsim/runs/c-astro-r25-studio-r8/20261027T223730Z-95543 seq3。
