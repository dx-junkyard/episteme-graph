---
id: IK-0952
title: "原稿スタジオのコースの見出しは、トピック教材がまだ無いと音声の事実文から旧いチャンクの状態語に落ちて内部語「（チャンクなし）」を出し、章の見出しは章題に既に「第N章」があっても「第N章: 」を前置して「第1章: 第1章 Cep B…」と二重になる"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "教員が原稿スタジオでコースを開き、見出しからコースの状態と章立てを読む"
  layers: [lecture_studio, frontend_admin_ui]
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
    処理軸 wording: frontend/public/js/admin-lecture-studio.js lsRenderCourseTree（1061〜1068 行）は topic_audio_status が no_topics のとき course_status の語彙
    （no_chunks:「チャンクなし」）へ落ちる。トピック教材で配信するコースではチャンクは使わないので、状態の説明にならない内部語が出る。1086 行は
    「第」+ 番号 + 「章: 」+ 章題で、コースビルダーが「第1章 …」と題名に番号を含めた章で番号が二重になる。接続軸 meaning（medium）: 章題に番号を含めるかの
    約束が生成と表示で揃っていない。構造軸 none。統制軸 none。
generalization:
  level: instance
  general_form: "新しい状態語が使えないときに旧い仕組みの状態語へ落ち、利用者に関係しない内部の語が見出しに出る／前置の番号が題名の番号と重なる"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: >-
    te-03 がコース内容生成前のコースを開くと見出しが「（チャンクなし）」で「何を意味するのか分からない」、te-04 が自分のコースで「第1章: 第1章 Cep B…」と
    二重だと書いた。run: uxsim/runs/c-astro-r25-studio-r3/20261023T071024Z-19157（seq4）/ uxsim/runs/c-astro-r25-studio-r3/20261023T071920Z-61773（seq4）。
    保存後・音声準備の行の「（生成済み）」はハーネスの投影の食い違い（harness_fixes_studio.md 第 3 周 H18）。
resolution:
  perspective: [single_point_fix]
  note: "見出しの旧い状態語から「チャンクなし」を外し、no_topics でもサーバの事実文（topic_audio_status.message）を出す。章題が「第N章」で始まるなら前置しない（lsChapterHeading）。"
  landed_in:
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/tests/test_lecture_studio_round25_r3.py"
  verification:
    methods: [guardrail]
    unverified:
      - "見出しは実画面で見ていない"
related: [IK-0877]
view_of: []
history: []
---

## 課題

症状: 見出しに「チャンクなし」と出る。章が「第1章: 第1章 …」になる。

原因: 状態の事実文が無いとき旧い状態語へ落ちる。章番号の前置が題名の番号を見ない。

## 発見の観点

コース内容生成前のコースと生成済みのコースを原稿スタジオで開いた。

## 解決の観点

第 25 周 面 D 第 3 周の是正（2026-10-23）で解決。見出しの旧い状態語から「チャンクなし」を外し、no_topics でもサーバの事実文（topic_audio_status.message）を出す。（以下は起票時の見立て）no_topics の事実文を足し、番号の前置を題名に合わせる。

## 一般化

落ちる先の語が利用者の語かを確かめる。確かめ方は、トピック教材の無いコースと「第N章」を含む章題のコースを開き、見出しを読むこと。
