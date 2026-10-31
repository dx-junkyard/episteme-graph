---
id: IK-0877
title: "授業用ドラフトの保存はそのトピックの生成済み音声を毎回消すのに、普通の保存では前にも後にも何も言わず（監査は「保存前に告知される」と記す）、音声の準備は「押せる／押せない」だけ、コースの状態はチャンクの数（「生成済み (231/231)」「チャンクなし」）で、配信しているトピック教材の状態を映さない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員がトピックの原稿を直して保存し、学生の講義の音声を作り直す必要があるかを確かめる"
  layers: [lecture_studio, tts_voice, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: backend/api/routes/lecture_studio/topics.py の保存（428〜436 行）は内容の変化を問わず
    topic_lecture_audio_cache を DELETE するが、応答は {course_id, topic_id, status} だけで「消した／元から無かった」を返さず、
    admin-lecture-studio.js（8997 行）は「授業用ドラフトを保存しました」だけを出す。告知は図の採用直後（admin-figure-studio.js の ADOPT_NOTICE）
    だけで、監査の metadata は「教員には保存前に告知される」と書く（462〜463 行のコメント。普通の保存には当たらない）。
    構造軸 representation（medium）: コースの状態（course-structure の course_status / generated_chunks / total_chunks）はチャンクの原稿と
    音声だけで数え、トピック教材の経路（lecture_uses_topic_material）の準備状態を表す値が画面に無い。音声生成ボタンは押せる理由も、
    どのトピックの音声が消えたかも言わない。統制軸 none（消すこと自体は表示と音声の食い違いを防ぐ既定の方針）。
generalization:
  level: repo_pattern
  general_form: "保存の副作用で派生物を消すのに、その事実を保存の応答と画面に載せず、状態表示は配信していない経路の単位で数える"
pattern: destructive-action-without-confirmation
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員ペルソナが保存の前後で音声生成ボタンの表示が全く同じで、作り直しが要るか読めなかったことを記した。面の審判（studio.py T6）も
    同じ点を所見にした。保存の SQL・応答・画面の文言・監査の文言を突き合わせた（設計時の疑い 4 の実測）。
    run: uxsim/runs/c-astro-r25-studio-r1/20261022T123418Z-31804（seq4・10・11・20）・uxsim/runs/c-astro-r25-studio-r1/20261022T125145Z-40602（seq11）。
resolution:
  perspective: [carry_through, first_class_state]
  note: "保存の DELETE の rowcount を audio_invalidated で返し、消したときだけ notice（TOPIC_AUDIO_INVALIDATED_NOTICE）を添えて画面が保存の結果に続ける。音声のあるトピック（course-structure の has_topic_audio）では保存の近くに TOPIC_AUDIO_SAVE_NOTICE を常に出す。コースの状態はトピック教材の経路ではトピック単位の topic_audio_status（none / partial / all の事実文・件数なし）で言い、チャンクの数を出さない。監査の topic_audio_cache_invalidated は実際に消したときだけ True、コメントを実際に合わせた。"
  landed_in:
    - "backend/api/routes/lecture_studio/topics.py"
    - "backend/core/lecture_studio_state.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "根拠の状態・トピックの論文・音声の有無をサーバの 1 箇所（core/lecture_studio_state と course-structure）で決め、画面は返った値と文を描くだけにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "音声生成ボタンの押せる／押せないの理由文（Issue #491 のフロー）は変えていない"
related: [IK-0879]
view_of: []
history: []
---

## 課題

症状: トピックを保存すると「授業用ドラフトを保存しました」とだけ出て、保存の前後で音声生成ボタンの表示は全く同じ。保存で
そのトピックの音声が消えたのか、元から無かったのか、作り直しが要るのかが読めない。コース名の横には「生成済み (231/231)」
（チャンクの数）や「チャンクなし」と出る。原因: 保存の副作用が応答と画面に運ばれず、状態表示がチャンクの経路で数えられる。

## 発見の観点

ペルソナの通し受講（reproduction）と保存の処理・文言の追跡（trace_walk）。

## 解決の観点

解決済み（2026-10-23）。消した事実を応答で返して画面に出し、状態はトピックの経路で数える。

## 一般化

副作用の事実を運ばない型。確かめ方は、音声のあるトピックと無いトピックをそれぞれ保存し、前者だけに消した事実文が出ることを見ること。
