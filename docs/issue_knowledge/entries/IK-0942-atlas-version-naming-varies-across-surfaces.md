---
id: IK-0942
title: "分野の地図の版が画面ごとに別の呼び名で出る（「公開版（版 2026.1）」「骨格 版2026.1」「骨格版 2026.1」「版 2026.1」）うえ、管理画面の分野の地図タブは「学習者に公開中: 版 2026.1」を要約カードと状態行で重ねて言う"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_vector_anchoring_design.md"
feature_context:
  realizing: "教員と学習者が、いま見ている地図・索引・糸・配置がどの版の地図に基づくかを読む"
  layers: [field_atlas_s, vector_anchoring_va, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [none]
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
    処理軸 wording: 版の言い方を各面が自前で組む — admin.js vectorRefreshFactText は「（骨格 版X）」、索引の facts は
    「現在の公開版（版 X）」、学習者の糸は「骨格 版X」、出典タブ・landscape-layer は「骨格版 X」、分野の地図タブは
    「版 X」。VA8・RE2・LS8 が「骨格版を明示」と求めるのに表示語の正本が無い。分野の地図タブは renderAtlasOverview の
    要約カード（学習者に公開中 = 版 X）と renderState の状態行（学習者に公開中: 版 X ／ 作成方法…）が同じ事実を重ねて描く。
    構造軸 representation: 版の表示語を 1 つの関数・定数で持っていない。接続軸 none・統制軸 none。
    確認: run 20261023T070217Z-13812 seq 2 / 4、run 20261023T070701Z-16885 seq 5 / 7 / 8、run 20261023T032742Z-99530 seq 2。
generalization:
  level: general
  general_form: "同じ事実（どの版か）の言い方を面ごとに組み立て、利用者が同じものか別のものかを迷う"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "精読型の教員が索引の結果文の「骨格」が公開版と同じものかを迷い、学生が出典タブと地図見出しの表記差を記した。 run: uxsim/runs/c-astro-r25-atlas-r3/20261023T070217Z-13812・20261023T070701Z-16885・20261023T032742Z-99530。"
resolution:
  perspective: [vocabulary_table]
  note: >-
    版の呼び名の正本を label_vocab.ATLAS_VERSION_PREFIX（「地図 版 」）/ atlas_version_label() に置き、サーバの索引の事実文（VECTOR_INDEX_*）と
    JS（admin.js atlasVersionText / landscape-layer.js / atlas-threads-layer.js / app.js /
    admin-release-review.js）が逐語ミラーで使う（一致はテストで固定）。分野の地図タブの状態行から「学習者に公開中: 版 X」を外し要約カードだけが言う。教員・学生のマニュアルの表記も揃えた。残課題:
    論文ディスカバリー・レーダー（admin-paper-discovery.js / admin-paper-radar.js・teacher/11 マニュアル）の「骨格 版」は面 C の外として残した。
    第 5 波（2026-10-27）: 教員側の残り — 凍結の確認モーダルの見出しを「地図 版 X を公開する前の確認」（admin.js・atlasVersionText）に揃えた。
    第 7 波（2026-10-28）: 論文の海に「地図 版 X」（corpus_view が skeleton_version_label を返し corpus-sea.js versionLineHtml が素通しで描く）、学習者の地図の下端も「骨格 X」から「地図 版 X」（atlas-overlay.js の逐語ミラー）。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/core/atlas_review_display.py"
    - "frontend/public/js/admin.js"
    - "frontend/public/js/landscape-layer.js"
    - "frontend/public/js/atlas-threads-layer.js"
    - "frontend/public/js/app.js"
    - "frontend/public/js/admin-release-review.js"
    - "docs/manual/student/02-student.md"
    - "docs/manual/teacher/17-admin-atlas.md"
    - "backend/tests/test_atlas_review_round25_r3.py"
    - "backend/tests/test_atlas_review_round25_r5.py"
    - "backend/core/corpus_view.py"
    - "frontend/public/js/corpus-sea.js"
    - "frontend/public/js/atlas-overlay.js"
    - "backend/tests/test_atlas_review_round25_r7.py"
  verification:
    methods: [guardrail]
    unverified:
      - "論文ディスカバリー・レーダーの版表記は未統一（面 C の外）"
related: [IK-0889]
view_of: []
history: []
---

## 課題

症状: 同じ版 2026.1 を、索引の区画は「現在の公開版（版 2026.1）」、索引の再構築の結果は「（骨格 版2026.1）」、学習者の糸は
「骨格 版2026.1」、出典タブは「骨格版 2026.1」と呼ぶ。教員は「骨格」が公開版と同じものかを迷った。分野の地図タブは
「学習者に公開中: 版 2026.1」を重ねて描く。

原因: 版の表示語の正本が無く、各面が自前で組み立てる。

## 発見の観点

通し受講（reproduction）で教員と学生がそれぞれ表記差に引っかかった。

## 解決の観点

第 3 波（2026-10-23）で是正。版の呼び名の正本を label_vocab.ATLAS_VERSION_PREFIX（「地図 版 」）/ atlas_version_label() に置き、サーバの索引の事実文（VECTOR_INDEX_*）と JS（admin.js atlasVersionText / landscape-layer.js / atlas-threads-layer.js / app.js / admin-release-review.js）が逐語ミラーで使う（一致はテストで固定）。
第 5 波（2026-10-27）で教員側の残り（凍結の確認の見出し）を「地図 版 X」に揃えた。

## 一般化

「どの版・どの骨格に基づくか」を明示する規律（VA8 / RE2 / LS8）を足すたびに、言い方の正本が無いと表記が増える。
確かめ方: 分野の地図を扱う全画面の文字列で版の言い方を走査し 1 形式であることを見る。

第 4 周の再演（2026-10-25・campaign c-astro-r25-atlas-r4）: 一部残存。教員画面・糸・出典タブの位置づけは「地図 版 2026.1」に揃った。学習者の地図オーバーレイのフッター（atlas-overlay.js「骨格 {版} / AI生成・教員レビュー済」）と詳細パネルの検証行（atlas_state.SEED_VERIFY_LINE「骨格（教員レビュー済）の初期表示」）が「骨格」のまま残り、同じ画面で糸の「地図 版」と並ぶ。run: uxsim/runs/c-astro-r25-atlas-r4/20261024T233738Z-85320 seq 5 / 6 / 8。

第 4 波（2026-10-25）: 残りを解消。学習者の地図オーバーレイのフッターは ATLAS_VERSION_PREFIX の逐語ミラー（atlas-overlay.js）で「地図 版 …」、検証行 SEED_VERIFY_LINE は「分野の地図（教員レビュー済）の初期表示」にした（test_atlas_review_round25_r4.py）。論文ディスカバリー・レーダーの表記は引き続き面 C の外。

第 5 周の再演（2026-10-27・campaign c-astro-r25-atlas-r5）: 学習者側は解消（オーバーレイの見出し「宇宙物理・地図 版 2026.2」、検証行「分野の地図（教員レビュー済）の初期表示」、糸・出典タブの「地図 版 2026.2」が揃った）。教員側の残り（軽）: 凍結の確認モーダルの見出しが「分野の地図骨格を公開前チェック」（admin.js の title）で、同じ画面の「地図 版 2026.2 を学習者向けに公開しました」と呼び方が揃わない。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T070623Z-23509 seq 13 / 15、uxsim/runs/c-astro-r25-atlas-r5/20261027T071720Z-78305 seq 5 / 8。


## 第 6 周の再演（第 25 周・面 C 第 6 周、2026-10-27・campaign c-astro-r25-atlas-r6）

GONE（教員側の残り）。凍結の確認モーダルの見出しが「地図 版 2026.3 を公開する前の確認」になった。学習者側（オーバーレイ・糸・出典タブ）も「地図 版 2026.3」で揃ったまま。残り（軽・起票しない）: 論文の海の画面には地図の版の表示が無い（地図と海が同じ版を見ているかは学生に読めない）。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T122716Z-6544 seq 17、uxsim/runs/c-astro-r25-atlas-r6/20261027T123201Z-9456 seq 5〜7、uxsim/runs/c-astro-r25-atlas-r6/20261027T123339Z-10273 seq 4。

## 第 7 周の再演（第 25 周・面 C 第 7 周、2026-10-28・campaign c-astro-r25-atlas-r7）

STILL（軽・海の画面）。論文の海に地図の版の表示が無い（同じ周の学生の地図は「地図 版 2026.4」）。教員側・学習者の地図・出典タブは「地図 版 2026.4」で揃う。run: uxsim/runs/c-astro-r25-atlas-r7/20261027T173037Z-59717 seq 4、uxsim/runs/c-astro-r25-atlas-r7/20261027T172454Z-57525 seq 5 / 7。

## 第 7 波の是正（2026-10-28）

第 7 波（2026-10-28）: 論文の海に「地図 版 X」（corpus_view が skeleton_version_label を返し corpus-sea.js versionLineHtml が素通しで描く）、学習者の地図の下端も「骨格 X」から「地図 版 X」（atlas-overlay.js の逐語ミラー）。

## 第 8 周の再演（2026-10-28・judge_atlas_r8）

**GONE**。論文の海（st-08・run uxsim/runs/c-astro-r25-atlas-r8/20261027T224529Z-99914 seq 4）の凡例の下に「地図 版 2026.5」が出て、学生の地図 L1 の見出し「地図 版 2026.5」と一致した。
