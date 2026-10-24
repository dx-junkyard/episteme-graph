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

## 一般化

「どの版・どの骨格に基づくか」を明示する規律（VA8 / RE2 / LS8）を足すたびに、言い方の正本が無いと表記が増える。
確かめ方: 分野の地図を扱う全画面の文字列で版の言い方を走査し 1 形式であることを見る。
