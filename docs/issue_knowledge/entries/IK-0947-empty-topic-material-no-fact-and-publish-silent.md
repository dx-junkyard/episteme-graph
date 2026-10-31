---
id: IK-0947
title: "本文の無いトピック（コース内容生成の前）を学生が開くと、教材は空白のまま事実文が出ず（preparation_notice は本文を返すときだけ）、講義のボタンは「📖 読む で教材を読めます」と空の読む表示へ誘導し、教員側も本文の無いトピックが大半のコースを開示範囲の変更だけで公開でき「更新しました」としか言わない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/release_review_flow_design.md"
feature_context:
  realizing: "学習者が公開されたコースのトピックを順に開き、まだ用意されていないトピックをそうと分かって飛ばす"
  layers: [learner_experience_b, lecture_player, frontend_learning_ui, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: backend/api/routes/learning.py get_topic_material（4802〜4815 行）は topic の本文が空でチャンクの救済も無いとき chunks=[] を返し、
    preparation_notice を「chunks があるときだけ」付ける（4814 行・コメント「本文を返さないときは付けない」）。core/lecture.py LECTURE_NO_SLIDES_NOTE（866 行）は
    読む表示の中身を見ずに「📖 読む で教材を読めます」と言う。本文が無いという事実がどの応答にも載らない。統制軸 completion（medium）: 開示範囲の PUT
    （/api/admin/courses/{id}/visibility）は本文の有無を見ずに公開し、応答は visibility だけで画面は「更新しました」。リリース前の確認（release_review）を経ない
    公開経路でトピックの準備状態が言われない。処理軸 none。構造軸 none。
generalization:
  level: repo_pattern
  general_form: "中身がまだ無い対象を空のまま配り、空であることも、空へ誘導していることも言わず、公開の操作もその状態を言わずに通す"
pattern: disclosure-not-declared
discovery:
  perspective: [reproduction, symptom_report]
  note: >-
    te-03 が種まきのコース（コース内容生成の前・18 トピック）を開示範囲の変更で公開し、st-01 がそのコースを受講して t1・t2 が真っ白、講義は押せず
    「読む で教材を読めます」で、2 回続けて空のため離脱した。run: uxsim/runs/c-astro-r25-studio-r3/20261023T071024Z-19157（seq21）/
    uxsim/runs/c-astro-r25-studio-r3/20261023T071646Z-49888（seq10〜12）。
resolution:
  perspective: [explicit_contract, carry_through]
  note: "教材が空なら preparation_notice に TOPIC_MATERIAL_EMPTY_NOTICE（生成中なら TOPIC_MATERIAL_EMPTY_PREPARING_NOTICE）を返し、学習画面は読み込み中と言わずに事実文を出す。講義の事実文 lecture_availability_note(has_reading_material=) は読む表示に本文があるときだけ「📖 読む で読めます」を言う。開示範囲の変更・クイック公開・リリース前の確認の公開の応答に、本文の無いトピックが残るなら readiness_note（COURSE_PUBLISHED_WITH_EMPTY_TOPICS_NOTICE・件数なし・公開は止めない）を返す。"
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/api/routes/learning.py"
    - "backend/core/lecture.py"
    - "backend/api/routes/lecture.py"
    - "backend/api/routes/admin.py"
    - "frontend/public/js/app.js"
    - "frontend/public/js/admin.js"
    - "frontend/public/js/admin-release-review.js"
    - "backend/tests/test_lecture_studio_round25_r3.py"
  verification:
    methods: [guardrail]
    unverified:
      - "コース内容生成の前のコースを公開して学生が開く流れは実機で見ていない"
related: [IK-0375, IK-0879]
view_of: []
history: []
---

## 課題

症状: 学生がトピックを開くと何も出ず、準備中か失敗か分からない。講義のボタンは空の読む表示を案内する。教員は本文の無いコースを公開しても何も言われない。

原因: 本文が無いという事実を、教材の応答・講義の事実文・公開の応答のどれも運ばない。

## 発見の観点

教員の公開と学生の受講を同じ周でつないだ。

## 解決の観点

第 25 周 面 D 第 3 周の是正（2026-10-23）で解決。教材が空なら preparation_notice に TOPIC_MATERIAL_EMPTY_NOTICE（生成中なら TOPIC_MATERIAL_EMPTY_PREPARING_NOTICE）を返し、学習画面は読み込み中と言わずに事実文を出す。（以下は起票時の見立て）空のときの事実文を 1 つ足し、案内文は読む表示の中身に合わせ、公開の応答に準備状態を返す。

## 一般化

空を配るときは空だと言う。確かめ方は、コース内容生成の前のコースを公開し、学生がトピックを開いたときに用意されていない事実文が出ることを見ること。
