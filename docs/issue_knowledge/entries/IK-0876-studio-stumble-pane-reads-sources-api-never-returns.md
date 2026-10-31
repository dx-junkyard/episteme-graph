---
id: IK-0876
title: "原稿スタジオのつまづき・図スタジオの論文の選び方が、course-structure の応答に無い sources を読むため、コース内容生成前のコースでは「紐づく解析済みドキュメントが見つかりません」、複数論文のコースでは選択中（無ければ先頭）のチャンクの論文に固定される"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員が開いているトピックの論文について、学生のつまづきの集計や図の材料を見る"
  layers: [lecture_studio, reconstruction_r, teaching_figures, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract, target]
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
    接続軸 contract: frontend/public/js/admin-lecture-studio.js の lsCoursePrimaryDocumentId（2214 行）と lsCourseDocumentIds（3005 行）は
    lsState.courseStructure.sources（または data.sources）を読むが、backend/api/routes/lecture_studio/topics.py の
    get_lecture_studio_course_structure の戻り値（204〜213 行）に sources は無い。接続軸 target: そのため lsLoadStumbleSummary（2190〜2191 行）
    は lsCurrentDocumentId（選択中のチャンク → 原稿一覧の先頭チャンクの論文）に頼り、トピックの論文ではなく先頭チャンクの論文を見る。
    原稿一覧が空（コース内容生成前）なら空になり「解析済みドキュメントが見つかりません」と出る（論文は completed）。図スタジオの
    documentIds も同じ関数。処理・構造・統制 none。
generalization:
  level: general
  general_form: "画面が応答に含まれないキーを読み、欠けているときは手近な別の対象に倒れるため、誤った対象か「無い」を事実として出す"
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員ペルソナが、completed の論文 4 本から作った直後のコースで「解析済みドキュメントが見つかりません」と言われたのを記した。
    フロントが読むキーと course-structure の戻り値を突き合わせた（設計時の疑い 7 の実測）。run: uxsim/runs/c-astro-r25-studio-r1/20261022T125145Z-40602（seq14）。
resolution:
  perspective: [carry_through, explicit_contract]
  note: "course-structure にコースの論文（documents = sources から解決した document と題名）と各トピックの論文（document_ids = 学ぶ単位の live 行 → content_blocks の部品 → 教材チャンクの document。コースの論文の外は返さない。何も無くコースの論文が 1 本ならその 1 本）を足した。つまづき・図スタジオはトピックの論文を先に使い（lsTopicDocumentId / lsCoursePrimaryDocumentId / lsCourseDocumentIds）、決まらなければ topic_document_unresolved_notice を出す。"
  landed_in:
    - "backend/api/routes/lecture_studio/topics.py"
    - "backend/core/lecture_studio_state.py"
    - "frontend/public/js/admin-lecture-studio.js"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "根拠の状態・トピックの論文・音声の有無をサーバの 1 箇所（core/lecture_studio_state と course-structure）で決め、画面は返った値と文を描くだけにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "completed の論文で作ったコース内容生成前のコースでつまづきが開けることは実機で見ていない"
related: []
view_of: []
history: []
---

## 課題

症状: completed の論文から作った直後のコースで、つまづきペインが「このコースに紐づく解析済みドキュメントが見つかりません」と言う。
複数論文のコースでは、開いたトピックとは別の論文（原稿一覧の先頭チャンクの論文）の記録を見る。原因: 画面が読む sources を
course-structure が返さず、欠けると手近なチャンクの論文に倒れる。

## 発見の観点

ペルソナの通し受講（reproduction）と画面と API の契約の突合（trace_walk）。

## 解決の観点

解決済み（2026-10-23）。API がトピックの論文を返し、画面はそれだけを使う。

## 一般化

応答に無いキーを読む片側の契約変更。確かめ方は、コース内容生成前のコースと 2 本目以降の論文のトピックでつまづきを開き、
そのトピックの論文の記録（または事実文）が出ることを見ること。
