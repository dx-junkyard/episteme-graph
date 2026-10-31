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
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "backend/tests/test_lecture_studio_round25_r5.py"
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

第 4 周の再演: 文言と確認は GONE（教員 te-02 が教材欄に区切りを 1 つ入れるとインジケータが「区切りがあるため自動ページ分割は止まっています」「読み上げ欄にも同じ数の区切りを入れると 2 枚に分かれます」「いまのまま保存すると 1 枚に統合して配信されます」を言い、保存の前にも同じ文で確認した）。ただし勘で押す教員は読まずに確認を通し、自動分割の 3 枚が 1 枚になって確定した — 規約（教員の区切りが優先・数が合わなければ 1 枚）と「区切りを足すと枚数が減る」構造は残る。区切りを入れる操作が読み上げ欄の対応位置を扱わない（ボタンの説明は「もう一方には自動挿入されない」）。run: uxsim/runs/c-astro-r25-studio-r4/20261024T232812Z-98850 seq7〜9。改善案（不変条項の範囲）: ［+ スライド区切りを挿入］を教材欄で押したとき、読み上げ欄の区切りの数が合わなくなるなら、その場で「読み上げ欄の同じ位置にも入れる」を選べるようにする（位置は段落の比で決め、教員が直す）。

第 4 波では是正していない（2026-10-25）: 境目を決める入口（IK-0975）・読みの単位（IK-0976）とは別の根（区切りの挿入の UI が読み上げ欄を扱わない）なので束ねなかった。改善案は上記のまま第 5 周以降。

第 5 周の再演（2026-10-27）: 文言は GONE のまま、構造は残る。te-01 はインジケータと保存の確認で「1 枚に統合して配信」を読んだうえで、読み上げ欄の区切りが 1 つ足りないまま保存した（場面の手順の形）。学生 2 人には t16 が 1 / 1 で届き、長い式のトピックが 1 画面に詰まる理由は学生側に出ない（統合は情報を落とさない設計どおり）。改善案は前周と同じ（区切りの挿入で読み上げ欄にも入れるかを選べる）。run: uxsim/runs/c-astro-r25-studio-r5/20261027T070623Z-23494 seq8〜11、uxsim/runs/c-astro-r25-studio-r5/20261027T072111Z-9659 seq8。

第 5 波で是正（2026-10-27・構造）: 規約（教員の区切りが優先・数が合わなければ 1 枚）は不変のまま、原稿スタジオに［+ 両方の欄に区切りを挿入］（教材欄のカーソル位置に入れ、読み上げ欄を揃える）と、統合の告知が出ているときだけ出る［読み上げ欄の区切りを教材欄に揃える］を足した（`lsAlignSpokenSlideMarkers` — 読み上げ欄の区切りを外し、教材欄の内側の区切りの相対位置に近い段落の境目（足りなければ文の境目）へ入れ直す。入れきれなければ事実文）。アンカー `lecture-studio.insert-slide-marker-both` / `lecture-studio.align-slide-markers` + マニュアル節。未確認: 実機の操作（node の無い環境で JS は構文検査と静的検査だけ）。

第 6 周の再演（2026-10-27）: **GONE**。区切りの無い t16（自動 7 枚・読み上げはタイマー送り）で教材欄に区切りを 1 つ入れると、インジケータが「1枚に統合して配信されます。区切り（===）があるため、自動ページ分割は止まっています」と次の操作を 1 行で言い、［読み上げ欄の区切りを教材欄に揃える］の 1 押しで「表示 2 枚 / 読み上げ 2 区切り ✓」になった。保存後、学生の講義も 2 枚（読み取りで確認）。頭脳の「7 枚が 2 枚になるのは少し意外」は規約どおり（教員の区切りが優先）。run: uxsim/runs/c-astro-r25-studio-r6/20261027T122226Z-3522 seq6〜10。

第 7 周の再演（第 25 周・面 D 第 7 周・版ありのコース、2026-10-28）: **GONE 継続**。te-01 が区切りの無い t16（表示 7 枚・読み上げはタイマー送り）で［+ 両方の欄に区切りを挿入］→ 教材欄に区切り 1 つで「1枚に統合して配信されます。区切り（===）があるため、自動ページ分割は止まっています」と次の操作が出て、［読み上げ欄の区切りを教材欄に揃える］1 押しで「表示 2 枚 / 読み上げ 2 区切り ✓」。発行（版 2）の後、学生の講義も 2 枚（周またぎの審判）。揃えた後の 2 枚目が長い 1 枚になる件は規約どおりで、長さの観点は第 8 周に見る。run: uxsim/runs/c-astro-r25-studio-r7/20261027T171357Z-51101 seq6〜9。

第 8 周の再演（2026-10-28）: **GONE 継続**。区切り 1 つで「1枚に統合…読み上げ欄にも同じ数の区切りを入れると 2 枚」+［読み上げ欄の区切りを教材欄に揃える］1 押しで 2 / 2 ✓。揃えた後の 2 枚目が残り全部を抱える長い 1 枚になるが長さの事実は出ない（教員の明示の区切り — 規約どおり・記帳は見送り）。run: uxsim/runs/c-astro-r25-studio-r8/20261027T223243Z-92722 seq7・8。
