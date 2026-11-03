---
id: IK-0991
title: "下書きから凍結した版 2026.2 の「作成方法」が、前の版の「同梱の基準地図から取り込み」のまま出る — 次版の下書きを作るとき前の版の generated_by を写し、版ごとの作り方を記録しない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/category_gap_candidates_design.md §5.5"
feature_context:
  realizing: "教員が次版の下書きを作って凍結し、公開中の版がどう作られたかを分野の地図タブで確かめる"
  layers: [field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [none]
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
    構造軸 representation: routes/atlas.py の draft/from-frozen（と骨格の draft 作成）は凍結版の generated_by をそのまま写し、凍結時にも書き換えない。接続軸 meaning: atlas_review_display.generated_by_label はその値を「作成方法」として公開中の版に付けるので、1 つの欄が『分野の地図が最初にどう作られたか』と『この版がどう作られたか』の 2 つの意味を兼ねる。履歴（changelog の note）は版ごとにあるので、作成方法だけが版と食い違う。処理軸 none、統制軸 none（誰が確認したかは確認者欄が凍結者に更新される）。
generalization:
  level: repo_pattern
  general_form: "版ごとの来歴の欄が前の版から複製され、新しい版の作られ方ではなく系列の起点の作られ方を言う"
pattern: same-name-different-referents
discovery:
  perspective: [reproduction, trace_walk]
  note: "凍結 2026.2 の直後、教員が『2026.2 は下書きから作った版なのに作り方の説明が前の版のものに見える』と記した。from-frozen が generated_by を写すことを確かめた。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T070623Z-23509 seq 2（下書きの generated_by が reference_map）/ 15 / 16。"
resolution:
  perspective: [representation_change]
  note: >-
    from-frozen が前の版の generated_by を写さず revision_of:{前の版}（atlas_review_display.revision_generated_by）を骨格の内容と DB 列の両方に記す。generated_by_label は「前の版（地図 版 X）からの改訂（候補・関係の取り込みと教員の編集）」に写す（トークンは出さない）。系列の起点の作られ方は履歴行が言う。
  landed_in:
    - "backend/api/routes/atlas.py"
    - "backend/core/atlas_review_display.py"
    - "docs/manual/teacher/17-admin-atlas.md"
    - "backend/tests/test_atlas_review_round25_r5.py"
  verification:
    methods: [guardrail]
    unverified:
      - "既に凍結済みの 2026.2 の作成方法は変わらない（次の下書きから効く）"
related: [IK-0850, IK-0942]
view_of: []
history: []
---

## 課題

症状: 凍結後の分野の地図タブが「学習者に公開中: 地図 版 2026.2」と並べて「作成方法: 同梱の基準地図から取り込み」と言う。
2026.2 は前の版から下書きを作り、候補と関係を取り込んで凍結した版である。

原因: 次版の下書きが前の版の generated_by を写し、凍結もそれを変えない。

## 発見の観点

凍結が初めて成立した周に教員が記した（reproduction）。下書き作成の経路を辿った（trace_walk）。

## 解決の観点

第 5 波（2026-10-27）で解決。作成方法を版ごとに記す。

## 一般化

版を重ねる構造の来歴欄は、版の作られ方と系列の起点を別に持つ。確かめ方: 2 版目を凍結した直後の作成方法の表示を見る。



## 第 6 周の再演（第 25 周・面 C 第 6 周、2026-10-27・campaign c-astro-r25-atlas-r6）

GONE（次の版から）。te-01 の凍結 2026.3 の後、見出しは「作成方法: 前の版（地図 版 2026.2）からの改訂（候補・関係の取り込みと教員の編集） ／ 確認者: 水野 早紀」。残り: 是正前に凍結した 2026.2 の記録は変えない方針のため、凍結前の画面（te-03・te-02・te-01 の seq 2）では 2026.2 の作成方法が「同梱の基準地図から取り込み」のまま出た — 既存の版の誤った記録は残る（変えるなら表示時の読み替えで、オーナー判断は要らない）。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T122716Z-6544 seq 2 / 18 / 19、uxsim/runs/c-astro-r25-atlas-r6/20261027T122226Z-3516 seq 2。

## 第 7 周の再演（第 25 周・面 C 第 7 周、2026-10-28・campaign c-astro-r25-atlas-r7）

GONE（記録）。2026.4 の凍結後「作成方法: 前の版（地図 版 2026.3）からの改訂… ／ 確認者: 西川 智也」。残り: 下書きを作る操作の入口は候補 0 件の画面に無く、教員が自分で下書きを作る観察は 7 周とも無い → NEW IK-1044。run: uxsim/runs/c-astro-r25-atlas-r7/20261027T171933Z-54352 seq 12、uxsim/runs/c-astro-r25-atlas-r7/20261027T171357Z-51096 seq 2。
