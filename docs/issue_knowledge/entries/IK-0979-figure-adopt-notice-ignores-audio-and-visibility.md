---
id: IK-0979
title: "教材図スタジオで図を採用したときの知らせは固定文で、コースに音声が 1 つも無くても「保存するとこのトピックの生成済み音声は作り直しになります」と言い、非公開のコースでも「トピックを保存するまで学習者には表示されません」（＝保存すれば表示される）と言う — 原稿スタジオの保存側は音声の有無と届き方（delivery）を見て言い分けるのに、採用側はそれを読まない"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/teaching_figure_studio_design.md"
feature_context:
  realizing: "教員が説明図を採用して保存したとき、いつ・誰に届き、音声に何が起きるかを採用の時点で知る"
  layers: [teaching_figures, lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [condition]
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
    接続軸 condition: frontend/public/js/admin-figure-studio.js の ADOPT_NOTICE（60〜62 行）は固定文で、採用の後（601・991 行）に
    そのまま出る。原稿スタジオの保存側は course-structure の has_topic_audio（IK-0877）と delivery（IK-0915 — 受講者のいない非公開
    コースでは何も言わない）を読んで言い分けるが、その値が図スタジオへ渡らない。処理軸 wording: 実状態と食い違う文になる。
    構造軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "同じ結果（保存で何が起きるか）を言う知らせが画面ごとにあり、一方だけが状態を見て言い分け、もう一方は固定文のまま食い違う"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: >-
    教員 te-02 が非公開・音声なしの自分のコース 0d70b5d0 の t0 で図を採用すると、音声の作り直しと「保存するまで学習者には表示されません」が
    出た。保存の知らせは「保存しました」だけで、受講者にまだ見えないこと（非公開）は採用の文からも保存の文からも読めなかった。第 3 周の
    te-03 でも同じ固定文が出ていた（judge_studio_r3.md §3 の記帳見送り）。
    run: uxsim/runs/c-astro-r25-studio-r4/20261024T232812Z-98850（seq18〜20）/ uxsim/runs/c-astro-r25-studio-r3/20261023T071024Z-19157。
resolution:
  perspective: [canonical_source, carry_through]
  note: "図スタジオの固定文 ADOPT_NOTICE をやめ、course-structure の figure_adopt_notice_parts（lead / audio / delivery。delivery は保存の届き方と同じ文、届く経路の無いコースは TOPIC_DELIVERY_NONE_NOTICE）とトピックの has_topic_audio を原稿スタジオが FigureStudio.open に渡し、図スタジオが組む。音声の無いトピックでは作り直しを言わず、非公開のコースでは「保存しても受講者には見えません」と言う。"
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "frontend/public/js/admin-figure-studio.js"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
    - "backend/tests/test_lecture_studio_round25_r4.py"
  verification:
    methods: [guardrail]
    unverified:
      - "音声あり・公開中のコースと音声なし・非公開のコースで採用の知らせが保存側と同じになるかは実機で見ていない（第 5 周 te-04）"
      - "採用の後に保存して音声が消えたとき、同じモーダルを開き直すまで部品は前の値のまま"
related: [IK-0877, IK-0915, IK-0880]
view_of: []
history: []
---

## 課題

症状: 音声の無いコースでも「音声は作り直し」、非公開のコースでも「保存するまで学習者には表示されません」と出る。教員は保存で学生に
届いたと思う。

原因: 採用の知らせが固定文で、保存側が読む音声の有無と届き方の値を読まない。

## 発見の観点

教員の通し受講（reproduction）で、採用 → 保存 → 公開と歩いたときの知らせの差として記された（第 3 周に続き 2 回目）。

## 解決の観点

第 25 周 第 4 波で是正（2026-10-25）。図スタジオの固定文 ADOPT_NOTICE をやめ、course-structure の figure_adopt_notice_parts（lead / audio / delivery。

## 一般化

同じ結果を言う知らせは状態を見る 1 か所から組む。確かめ方: 音声なし・非公開のコースと、音声あり・公開中のコースで採用し、知らせが
保存側の事実文と同じになることを見る。

第 5 周の再演（2026-10-27）: **GONE**（音声なし・公開中・版なしのコース）。te-01 が 6be229e1 の t16 に図を採用すると「図を教材欄に入れました。トピックを保存すると確定します。 このコースは版を発行していないので、保存した内容はすぐ受講者に届きます。」で、音声の作り直しは言わず（音声なし）、保存側の知らせと同じ文だった。音声あり・非公開の組は未確認。run: uxsim/runs/c-astro-r25-studio-r5/20261027T070623Z-23494 seq20。

第 7 周の再演（第 25 周・面 D 第 7 周、2026-10-28）: **未評価**。今周は図の採用の場面を走らせず、音声も全トピック未生成のままで、音声ありの組（保存による音声の作り直しの告知）は 7 周を通して見ていない。第 8 周で 1 トピックの音声を先に生成してから見る（judge_studio_r7.md §7）。

第 8 周（2026-10-28）: **未評価**（音声は全トピック未生成・図の採用の手なし）。
