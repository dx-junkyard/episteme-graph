---
id: IK-1010
title: "教員が図スタジオで採用して保存した説明図が、原稿スタジオの根拠リンクで「対応付けられていない候補（同じ節から拾った式・根拠）」の見出しの下、解析由来の式・主張の後ろ（t16 では 32 件中 28 番目）に並び、トピックの状態は「対応なし」のまま — 教員が自分で入れた図が解析の拾い残しと同じ扱いで描かれる"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員が説明図を採用して保存した後、その図がトピックの資料として数えられていることを原稿スタジオで確かめる"
  layers: [lecture_studio, teaching_figures, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
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
    接続軸 meaning: 採用時の登録（topic.evidence_links のトップレベルに figure_id・extra.teaching=true）は正しく入り、evidence_items にも
    kind=figure で出る（読み取りの API で確認）。ところが evidence_state は解析の対応付けだけから導出され、"missing" のときの見出し
    unmapped_heading「対応付けられていない候補（同じ節から拾った式・根拠）」が教員の図を含む一覧全体に掛かる。処理軸 wording: 一覧の見出しが
    中身の一部（教員が入れた図）について誤った出所を言う。構造軸 none（項目の表現は足りている）。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "人が加えた項目と機械が拾った候補が同じ一覧に入り、機械側の状態から作った見出しが人の項目にも掛かる"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    教員 te-01 が t16 に図を採用して保存した後、「根拠リンクは『対応なし』のままで、いま入れた図のことは何も出ていない」と記した
    （ハーネスの投影は候補を 10 件で切っていたので図は見えなかった — harness_fixes_studio.md H28）。読み取りの API で、図は evidence_items の
    28 番目にあり、見出しは unmapped_heading だった。t0（解析の対応あり）では同じ種類の図が「根拠あり」の下に出る。
    run: uxsim/runs/c-astro-r25-studio-r5/20261027T070623Z-23494 seq21。
resolution:
  perspective: [first_class_state, guardrail_fix]
  note: "topic_evidence_state が教員の加えた資料（role=teaching_figure / teaching）の区画の見出し teacher_added_heading を別に返し（根拠の状態の判定は不変）、原稿スタジオの中央のチップと右ペインの一覧は教員の資料を先頭の区画に教材欄の埋め込み位置順で並べ、unmapped_heading は解析の候補の区画にだけ掛ける（lsSplitTeacherAddedEvidence）。"
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "backend/tests/test_lecture_studio_round25_r5.py"
    - "backend/tests/test_lecture_studio_round25_r6.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "見出しの文はサーバの 1 箇所（topic_evidence_state）で出所ごとに返し、中央・右ペインの両面が同じ値を描く。固有の差分: 区画の並びは教材欄の埋め込み位置（本文の順）で決める。"
  verification:
    methods: [guardrail]
    unverified:
      - "固定コースの t16 で採用図が先頭の区画に出るかは実機で見ていない"
      - "スライド単位の区画（何枚目の図か）までは出していない — 教材欄の埋め込み順まで"
      - "根拠ありのトピック（t0）で解析の候補の区画に見出しが出るかは実機で見ていない（第 7 周 te-05）"
related: [IK-0910, IK-0949, IK-0875]
view_of: []
history: []
---

## 課題

症状: 採用した図が「同じ節から拾った候補」の下・末尾に並び、教員は図が資料として数えられているかを画面から判断できない。

原因: 根拠の状態と見出しが解析の対応付けだけから導出され、教員が入れた項目（extra.teaching）を別に扱わない。

## 発見の観点

教員の通し受講（reproduction）の疑問を、読み取りの API の生値で確かめた（data_inspection）。

## 解決の観点

解決済み（2026-10-27・第 5 波）。教員が加えた資料を解析の候補と別の区画にし、見出しを出所ごとに付ける。

（起票時の案）案: 教員が入れた項目（教材図）は一覧の先頭に「教員が加えた資料」の小見出しで分け、unmapped_heading は解析の候補にだけ掛ける。
evidence_state の判定は変えない（解析の対応付けの事実はそのまま）。

## 一般化

一覧の見出しは中身の出所ごとに付ける。確かめ方: 解析の対応が無いトピックに教材図を採用し、図が解析の候補の見出しの外に出ることを見る。

第 6 周の再演（2026-10-27）: **一部 STILL（根拠ありのトピック）**。根拠の状態が missing の t16（te-01）では教員の図が先頭の区画・解析の候補は unmapped_heading の下に分かれた（GONE）。状態が sufficient の t0（共有教員 te-04）では unmapped_heading が返らない（missing のときだけ）ので、解析の候補（前提式・英語の主張 8 件・原文抜粋・トピック概要）が見出しなしで「教員が加えた資料（教材欄に埋め込んだ順）」の直後に続き、頭脳は「見出しからは区別できない」と記した（中央のチップ・右ペインとも）。解析の候補の区画に、状態によらず見出しを付ける必要がある。run: uxsim/runs/c-astro-r25-studio-r6/20261027T122747Z-6889 seq4・5。

第 6 周の再演: 一部 STILL（judge_studio_r6 §2）。根拠の状態が missing の t16 は区画が分かれたが、sufficient の t0 では `unmapped_heading` が返らず、解析の候補が見出しなしで「教員が加えた資料」に続いた。第 6 波で是正（2026-10-27）: `topic_evidence_state` は教員の加えた資料があり `unmapped_heading` が出ないとき `analysis_heading`（`EVIDENCE_ANALYSIS_HEADING`「論文の解析から拾った根拠（式・主張・原文抜粋・概要）」）を返し、中央のチップと右ペインは解析の区画（`data-evidence-section="analysis"`）にそれを掛ける。根拠の状態の判定は不変。

第 7 周の再演（2026-10-28）: **GONE**。根拠ありの t0（共有教員 te-05）で「教員が加えた資料（教材欄に埋め込んだ順）」の後に見出し「論文の解析から拾った根拠（式・主張・原文抜粋・概要）」が付き、解析の候補が区切られた。missing の t16（te-01）は「対応付けられていない候補」の下（GONE 継続）。run: uxsim/runs/c-astro-r25-studio-r7/20261027T171857Z-53968 seq4・5、uxsim/runs/c-astro-r25-studio-r7/20261027T171357Z-51101 seq6。

第 8 周の再演（2026-10-28）: **GONE 継続**（te-05 の t0「論文の解析から拾った根拠」・te-01 の t16 は missing 形で「教員が加えた資料」の下に説明図）。run: uxsim/runs/c-astro-r25-studio-r8/20261027T223730Z-95543 seq4、uxsim/runs/c-astro-r25-studio-r8/20261027T223243Z-92722 seq6。
