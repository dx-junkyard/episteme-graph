---
id: IK-0878
title: "授業用ドラフトの教材欄にスライド区切り（===）を 1 つ入れると自動ページ分割が止まり、「表示 4 枚（自動ページ分割）」が「⚠ 表示 2 / 読み上げ 1 — 1枚に統合して配信されます」に変わる（区切りを足すと枚数が減る）が、その規約と揃え方を画面が言わず、統合のまま保存も確認なく通る"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/lecture_slide_sync_design.md"
feature_context:
  realizing: "教員が長いトピックの区切りを自分で調整し、表示と読み上げを揃えて保存する"
  layers: [lecture_studio, lecture_player, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [review]
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
    処理軸 wording: backend/core/lecture.py の auto_paginate_slides は display か spoken に === が 1 つでもあれば教員の明示分割を優先して
    自動分割を止め、分割数が合わなければ 1 枚に縮退する（設計どおり）。admin-lecture-studio.js の lsRenderSlideIndicatorEl（7834〜7857 行）は
    縮退の結果だけを言い、「区切りを入れると自動分割は止まる」「読み上げ欄にも同じ数の区切りが要る（いま読み上げは N 区切り）」を言わない。
    統制軸 review（medium）: 統合で配信される状態のまま保存できる（保存は警告を確かめない）。構造・接続 none（値は正しい）。
generalization:
  level: repo_pattern
  general_form: "既定の自動処理を手動の指定が止める規約があるのに、手動の指定を 1 つ入れたときに規約と揃え方を言わず、結果だけを警告する"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: >-
    教員ペルソナ（几帳面な新任）が区切りを 1 つ入れたら 4 枚が 1 枚に統合されると出て「逆に思えて驚いた」と記した。
    run: uxsim/runs/c-astro-r25-studio-r1/20261022T123418Z-31804（seq8〜10）。読み上げ欄の区切り位置が末尾になったのはハーネスの近似
    （harness_fixes_studio.md）で、規約が画面に出ないことは近似に依らない。
resolution:
  perspective: [explicit_contract, single_point_fix]
  note: "preview-split が auto_paginate_suppressed（区切りがあり自動ページ分割をしなかった）と notes（理由と揃え方の事実文。正本は core/lecture.slide_split_notes）を返し、インジケータがそれを描く。教材欄と読み上げ欄の区切りの数が合わず統合で配信される状態のまま保存するときは、同じ内容につき 1 回だけ確認する。分割の規約（教員の区切りが優先・数が合わなければ 1 枚）自体は不変。"
  landed_in:
    - "backend/core/lecture.py"
    - "backend/api/routes/lecture_studio/scripts.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "docs/manual/teacher/14-admin-lecture-studio.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "根拠の状態・トピックの論文・音声の有無をサーバの 1 箇所（core/lecture_studio_state と course-structure）で決め、画面は返った値と文を描くだけにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "保存前の確認ダイアログの操作は実機で見ていない"
related: [IK-0877]
view_of: []
history: []
---

## 課題

症状: 区切りを 1 つ入れたら枚数が 4 から 1 に減ったように見え、どこに区切りを入れれば揃うのかが画面から読めない。統合の警告が
出たまま保存できる。原因: 明示分割が自動分割を止める規約を、インジケータが結果としてしか言わない。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

解決済み（2026-10-23）。規約と揃え方をインジケータが言い、統合のまま保存するときに確かめる。

## 一般化

手動の指定が既定の自動処理を止める型の説明不足。確かめ方は、自動分割されるトピックに区切りを 1 つ入れ、インジケータが
止まった理由と必要な区切りの数を言うことを見ること。
