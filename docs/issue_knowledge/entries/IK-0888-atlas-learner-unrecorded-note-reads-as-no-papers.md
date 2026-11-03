---
id: IK-0888
title: "学習者の分野の地図で、論文の位置が置かれている項目にも「この場所について、コーパスの論文からの記帳はまだありません」と出て、論文が無いと読める"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/field_atlas_overlay_spec.md"
  - "docs/features/knowledge_landscape_design.md"
feature_context:
  realizing: "学習者が分野の地図の項目を選び、その場所の状態と論文の位置を読む"
  layers: [field_atlas_s, knowledge_landscape, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
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
    処理軸 wording / 接続軸 meaning: ピルの事実文 label_vocab.ATLAS_PILL_NOTES["unrecorded"] は D層の台帳（検証の記帳）が
    無いことを言う文だが、「コーパスの論文からの記帳はまだありません」の言い方は「この場所に論文が無い」と読める。同じ地図の
    「論文の位置」レイヤー（landscape の配置）はその項目に論文を置いている（ブラックホールに連星ブラックホールの論文）。
    構造軸 representation（medium）: ノードの状態語（未訪問 / 台帳に記帳なし）と配置の有無が別の層にあり、ノードの文が
    どちらの層の事実かを名乗らない。統制軸 none。確認: run 20261022T220438Z-77657 seq 5（black_holes の pill_note）と
    seq 6（論文の位置: ブラックホール）、学生ペルソナの「論文がある場所なのに記帳なしと出て混乱する」。
generalization:
  level: repo_pattern
  general_form: "ある層の『記録が無い』を言う文が、別の層の対象（論文・配置）の不在とも読める語で書かれ、同じ画面に並ぶ別の層の事実と矛盾して読める"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, symptom_report]
  note: "英語話者の学生ペルソナが地図の項目の文と論文の位置レイヤーを並べて読み、矛盾を書いた。 run: uxsim/runs/c-astro-r25-atlas-r2/20261022T220438Z-77657。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    label_vocab.ATLAS_PILL_NOTES["unrecorded"] を「この場所について、検証の記録（台帳）はまだありません。論文がこの場所に置かれているかどうかは「論文の位置」で示します。」に書き換え、文がどの層の事実かを名乗るようにした（配置レイヤーの名前「論文の位置」を指す）。ガードレールは文が「台帳」を含み「論文からの記帳」を含まないこと。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/tests/test_atlas_review_round25_r2.py"
  verification:
    methods: [guardrail]
    unverified:
      - "配置のあるノードと無いノードを並べた画面での読みは再演で未確認"
related: [IK-0412]
view_of: []
history: []
---

## 課題

症状: 学生が地図で「ブラックホール」を見ると「未訪問 — この場所について、コーパスの論文からの記帳はまだありません」と出る。
同じ画面の「論文の位置」ではブラックホールに論文が置かれている。学生は論文があるのか無いのか分からない。

原因: 台帳（検証）の不在を言う文が、論文の不在とも読める語で書かれている。

## 発見の観点

通し受講の再演（reproduction）での学生の所見（symptom_report）。

## 解決の観点

第 2 波（2026-10-23）で是正。文がどの層の事実かを名乗る言い方に直し、配置レイヤーと並べたときの矛盾をガードレールで固定する。

## 一般化

地図・グラフの 1 ノードに複数の層（検証・配置・個人の訪問）の状態が重なる画面で、層を名乗らない短文が別の層と矛盾して読める。
確かめ方: 配置があるノードと無いノードの両方で、ノードの文と位置レイヤーを並べて読む。
