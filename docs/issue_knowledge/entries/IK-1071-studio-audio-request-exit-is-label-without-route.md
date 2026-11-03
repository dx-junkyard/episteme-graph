---
id: IK-1071
title: "共有教員の原稿スタジオで音声生成が押せないとき「→ 所有者に依頼する」と次の操作のように出るが、押せる道も宛先の名前も依頼の中身も無い（書き換えの［案をコピー］とは非対称）"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "コースの editor 教員が、所有者だけができる音声生成（作り直し）を所有者に頼む"
  layers: [lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [completion]
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
    確定（コードで読んだ）: `admin-lecture-studio.js::lsAudioReadinessGuidance` の owner_only は action に固定文「所有者に依頼する」を返し、
    `lsRenderAudioReadinessNote` が `#ls-audio-readiness-action`（span）に「→ 所有者に依頼する」と書くだけ。他の state の action
    （「原稿生成を行う」等）は同じ画面で実行できる操作だが、owner_only だけは実行の手段が製品に無い。所有者名（owner_display_name）は
    同じ course-structure にあるが、この行には使っていない。書き換えの案は［案をコピー］+「所有者（名前）に渡してください」で
    受け渡しが閉じる（IK-0978）のに、音声の依頼は宛先も中身も運ばない。接続軸 information / 統制軸 completion。
generalization:
  level: repo_pattern
  general_form: "自分ではできない操作の代わりに『誰かに頼む』を次の操作として示すが、頼む相手・頼む中身・頼む手段のどれも持たない"
pattern: last-mile-missing
discovery:
  perspective: [trace_walk, symptom_report]
  note: >-
    第 25 周 面 D 第 8 周。te-05（6be229e1 の editor）の原稿スタジオのコース設定メニューに「このコースの音声生成は、…所有者だけが
    できます。音声の作り直しは所有者に依頼してください。 → 所有者に依頼する」。場面に押す手は無く、審判がフロントのコードで行き先を
    確かめた（span で押せない）。run: uxsim/runs/c-astro-r25-studio-r8/20261027T223730Z-95543 seq3。
resolution:
  perspective: [single_point_fix]
  note: >-
    第 25 周 第 8 波（2026-10-31）: 押せない「→ 所有者に依頼する」をやめ、［案をコピー］と同型の受け渡しにした。サーバ
    （`lecture_studio_state.topic_save_facts` の editor 側）が `audio_generate_notice` を所有者の表示名入り（「…所有者（{name}）に依頼して
    ください。」）にし、`audio_request`（札・宛先・依頼の 1 文・見出しの語・言語の語・写した後の知らせ）を返す。原稿スタジオ
    （`lsRenderAudioReadinessNote` / `lsCopyAudioRequest`）が理由の文の横に［依頼の文をコピー］を置き、【コース】【お願い】【読み上げの言語】
    【音声の状態】【音声の無いトピック】をクリップボードへ写す（数値なし・通知は送らない・DB 非変更）。cross_layer_notify に乗せる案は
    教員間の新しい通知の種類になる（オーナー判断の範囲）ため採らなかった。アンカー `lecture-studio.audio-request-copy` + マニュアル節 +
    ADMIN_UI_ANCHORS の 3 点セット。
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "backend/tests/test_lecture_studio_round25_r8.py"
    - "backend/tests/test_admin_help_ui_anchors.py"
  verification:
    methods: [guardrail]
    unverified:
      - "editor の画面でボタンが描かれ、写した文の宛先・見出しが揃うことはブラウザで見ていない（第 9 周 te-05）"
related: [IK-0978, IK-1053]
view_of: []
history: []
---

## 課題

症状: 音声生成が押せない共有教員に「→ 所有者に依頼する」と出るが、押せず、誰にどう頼むかが分からない。

原因: 他の未準備理由と同じ「理由 + 次の操作」の枠に、製品に手段の無い操作を入れた。

## 発見の観点

editor の画面を歩き（trace_walk）、出口の行き先をフロントのコードで確かめた（code_reading）。

## 解決の観点

第 8 波で是正（2026-10-31）。宛先の名前を入れた事実文にし、頼む中身は案のコピーと同じ部品で運ぶ。

## 一般化

「〇〇に頼む」を示すなら、相手・中身・手段を伴う（last-mile-missing）。確かめ方: editor で音声の行を開き、相手の名前と渡すものが画面で揃うかを見る。

## 第 9 周（第 25 周・面 D 第 9 周、2026-10-31・judge_studio_r9）

**GONE**。te-05 の音声生成の行が「…音声の作り直しは所有者（水野 早紀）に依頼してください。［依頼の文をコピー］」になり、押すと「音声の依頼の文をクリップボードに写しました。所有者（水野 早紀）に渡してください。」+ 見出し【コース】【お願い】【読み上げの言語】【音声の状態】【音声の無いトピック】。所有者名と中身が揃い、書き換えの［案をコピー］と対称になった。確かめていない範囲: クリップボードの中身はランナーが `lsCopyAudioRequest` と同じ組み立てで再現したもので、ブラウザの実クリップボードは見ていない。run: uxsim/runs/c-astro-r25-studio-r9/20261031T003210Z-30686 seq3・9・10。
