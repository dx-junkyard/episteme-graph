---
id: IK-0955
title: "ナレッジライブラリのエントリ詳細が、いまの扱いが「確定」のときにも［確定にする］を並べ、現状と操作の区別が付かない"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員がライブラリのエントリの扱い（候補・確定・見送り）を確かめ、必要なら変える"
  layers: [concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 logic: frontend/public/js/admin.js:3190-3197 は review_status
    に関係なく［確定にする］［見送る］を常に描く。確定済みの行に［確定にする］が並び（te-05 が「押す必要があるのか」と迷った）、見送り済みの行に［見送る］が並ぶ。表現・接続・手続は正しい（none）—
    状態に応じて操作を出し分ける分岐が無いだけ。
generalization:
  level: instance
  general_form: "状態の表示の横に、その状態へ移す操作を状態に関係なく並べる"
pattern: permission-and-affordance-asymmetric
discovery:
  perspective: [reproduction]
  note: >-
    te-05 が既存エントリの詳細で「いまの扱い: 確定」と［確定にする］を同時に読んで迷った（uxsim/runs/c-astro-r25-share-r4/20261024T232812Z-98848
    seq3）。admin.js の描画を読んだ。
resolution:
  perspective: [representation_change]
  note: >-
    エントリの扱いの状態ごとに操作を出し分けた（admin.js _libraryEntryReviewButtonsHtml）。候補 = ［確定にする］［見送る］、見送り済み = ［候補に戻す］（既存の restore 遷移）、確定済み = 操作なし（確定から先へ動く遷移は無い）。
  landed_in:
    - "frontend/public/js/admin.js"
    - "backend/tests/test_share_round25_r4_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "3 つの扱いのエントリを開いた実画面"
related: [IK-0925]
view_of: []
history: []
---

## 課題

症状: 確定済みのエントリに［確定にする］が出る。原因: 描画が review_status を見ずに 2 つのボタンを常に描く。

## 発見の観点

再現（te-05 の (a') seq3）。

## 解決の観点

是正済み（第 25 周・面 A 第 4 波・2026-10-25）。

旧案: 是正案: candidate なら［確定にする］［見送る］、confirmed なら［見送る］だけ（あるいは「候補に戻す」は設けない）、dismissed なら［確定にする］だけ。同じ文の「確定は凍結とは別の操作」の隣に、凍結の入口（［この版を凍結］）の位置を 1 文で添える。

## 一般化

状態と操作の対応表が描画に無い型。確かめ方は、3 つの扱いのエントリを開き、出るボタンが状態ごとに違うことを画面で見ること。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 消滅（確定後は［確定にする］が消えた）。確定の成功の知らせが無いことは IK-0982 に記す。run: uxsim/runs/c-astro-r25-share-r5/20261027T071135Z-43804 seq5
