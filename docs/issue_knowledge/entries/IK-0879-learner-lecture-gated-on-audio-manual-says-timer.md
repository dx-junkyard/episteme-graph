---
id: IK-0879
title: "学生の「🎙 講義」はトピックに音声が 1 枚も無いと押せず、ツールチップは学生に「管理画面で音声を生成してください」と言う。マニュアルは「音声未生成のスライドは一定時間で次へ進む（読むことはできる）」と書き、講義の再生側もタイマー送りを持つのに、音声ゼロのトピックでは入口で閉じる"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/manual/student/02-student.md"
feature_context:
  realizing: "学生がトピックを講義（スライド送り）の形で受講し、音声が無ければ文字で読み進める"
  layers: [lecture_player, frontend_learning_ui, docs]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [condition]
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
    接続軸 condition: frontend/public/js/app.js の updateLectureToggleAvailability（7084〜7103 行）は state.topicHasAudio（audio-status の
    has_audio = ready_slides > 0。backend/api/routes/lecture.py 476・500 行）が偽なら講義を無効化する。再生側は音声の無いスライドを
    タイマー送りにする（CLAUDE.md「音声なしスライドはタイマー送り」）ので、「1 枚でもあれば入れる・0 枚なら入れない」の条件が入口と再生で
    食い違う。処理軸 wording: 無効時のツールチップ（app.js 7101 行）は学生に取れない操作（管理画面）を指示する。統制軸 completion（medium）:
    マニュアル（student/02-student.md 705〜706 行）が実装と違う挙動を「できる」と書く。構造 none。
generalization:
  level: repo_pattern
  general_form: "部分的な欠落には縮退の手段があるのに、全部欠けたときだけ入口で閉じ、閉じた理由を利用者が取れない操作で説明する"
pattern: doc-drifts-from-code
discovery:
  perspective: [reproduction, doc_code_diff]
  note: >-
    学生ペルソナ 2 人（講義で聴きたい・音声中心）が 3 トピック続けて講義に入れず、ツールチップが管理画面を指すのを記した。マニュアルの
    記述と app.js の入口条件を突き合わせた（設計時の疑い 3 の実測）。run: uxsim/runs/c-astro-r25-studio-r1/20261022T125729Z-44102（seq7〜13）・
    uxsim/runs/c-astro-r25-studio-r1/20261022T125944Z-45915（seq7・8）。
resolution:
  perspective: [single_point_fix, doc_correction]
  note: "入口の条件を再生側と揃えた: audio-status の total_slides が 1 以上なら音声ゼロでも講義に入れ、全スライドをタイマー送り（既存の simulatePlayback）にする。学生に取れない操作（管理画面）を指示する文をやめ、サーバの lecture_note（core/lecture.lecture_availability_note。「担当の先生が準備します」「文字のスライドを時間で送ります」）を切り替えの横とツールチップに出す。学習者経路から音声を作らない規律は不変。マニュアル student/02-student.md#interactive-lecture に音声ゼロのときの挙動を足した。"
  landed_in:
    - "frontend/public/js/app.js"
    - "backend/api/routes/lecture.py"
    - "backend/core/lecture.py"
    - "docs/manual/student/02-student.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "根拠の状態・トピックの論文・音声の有無をサーバの 1 箇所（core/lecture_studio_state と course-structure）で決め、画面は返った値と文を描くだけにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "音声ゼロのトピックで講義を最後まで送る操作は実機で見ていない（第 2 周 st-07）"
related: [IK-0877]
view_of: []
history: []
---

## 課題

症状: 音声の無いトピックで「🎙 講義」が押せず、ツールチップは「管理画面で音声を生成してください」。学生は誰に何を頼めばよいか
分からず、文字だけのスライド送りもできない。マニュアルはタイマー送りで読めると書く。原因: 入口の条件（音声が 1 枚以上）と再生側の
縮退（音声なしはタイマー送り）が食い違い、文言が学生の取れない操作を指す。

## 発見の観点

ペルソナの通し受講（reproduction）とマニュアルと実装の突合（doc_code_diff）。

## 解決の観点

解決済み（2026-10-23）。音声ゼロでも入れるか、閉じるなら学生に向けた事実文とマニュアルに直す。

## 一般化

全欠落だけを入口で閉じる型。確かめ方は、音声ゼロ・一部・全部の 3 トピックで講義の入口と再生の挙動を並べ、マニュアルの記述と一致することを見ること。
