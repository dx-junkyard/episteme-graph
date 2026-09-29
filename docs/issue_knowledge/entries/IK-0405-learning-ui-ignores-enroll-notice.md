---
id: IK-0405
title: "受講登録の応答が成立の事実文（enrolled / notice）を持つようになった後も、学習画面はそれを読まず、登録直後の画面に成立の事実が1行も出ない"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が受講開始の操作が成立したことを画面から読める
  layers: [frontend_learning_ui, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    接続軸: サーバの enroll 応答（LearningEnrollOut）は IK-0385 で enrolled と事実文 notice を持つようになったが、
    app.js の enrollCourse は応答から id だけを読んでコースを切り替えていた。事実文という情報が応答から画面へ
    運ばれていない（information）。構造軸は、事実文を置く器が学習画面に無かったことを表現の欠落と読む余地が
    あるため medium で none とした（器を足すのは配線の一部と見た）。処理・統制は none。
generalization:
  level: repo_pattern
  general_form: 操作の応答に成立の事実文を足しても、それを読むべき画面側の配線が無く利用者に届かない
pattern: available-but-unwired
discovery:
  perspective: [trace_walk, reproduction]
  note: >-
    第 8 周の学生が登録後の応答を「受講できなかった」と読んだ件（IK-0385）の是正を、応答から画面まで辿った。
    応答には notice が載るが、enrollCourse が読まないため画面には何も出ないことが見えた。
resolution:
  perspective: [carry_through]
  note: >-
    トップバーのコース選択の右に事実文の器（#course-enroll-notice・既定 hidden・警告色にしない）を置き、
    enrollCourse がコース切替の後に data.enrolled のときだけ data.notice を textContent で描く。
    文言はサーバ正本（label_vocab.COURSE_ENROLLED_NOTICE）の素通しでフロントに焼き込まない。
    タイマーでは消さず、次のコース切替の冒頭で消す。操作要素ではないので data-ui-anchor は付けない。
  landed_in:
    - frontend/public/js/app.js
    - frontend/public/index.html
    - frontend/public/css/styles.css
    - docs/manual/student/02-student.md
    - backend/tests/test_learner_facts_wave6_ui_static.py
  verification:
    methods: [guardrail]
    unverified:
      - ブラウザでの実描画（トップバーの幅に収まるか・長い題名のコースで省略表示が効くか）
      - 砂場で実際に受講登録し、事実文が出て次の切替で消えること
related: [IK-0385, IK-0369]
view_of: []
history: []
---

## 課題

受講登録の応答は成立の事実文を持つが、学習画面がそれを読まないため、登録直後の画面に成立の事実が出ない。

## 発見の観点

応答から画面までを辿り、応答に載った値が描画に使われていないことを見た。

## 解決の観点

事実文の器を置き、応答の notice を後段（画面）まで運ぶ。

## 一般化

応答に事実文を足しても、読む側が配線されていなければ利用者には届かない。
