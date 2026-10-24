---
id: IK-0856
title: "学習画面の出典タブの入口「この分野の未検証の前提を見る」が、検証の不在を分野の範囲で言う（SL1 では言えるのは「このコーパスの中では」だけ）— 閉世界語彙のガードレールは定型句の完全一致だけを見ていて、この言い換えを通した"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "学生が受講コースの未検証の前提（台帳の投影）を出典タブから開く"
  layers: [doubt_d, stakes_ledger_sl, frontend_learning_ui, tests_guardrails]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [review]
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
    処理軸 wording: `frontend/public/js/app.js`（3090）の summary が「この分野の未検証の前提を見る」。中身は
    `compile_open_assumptions` のコース範囲の投影（台帳はコーパスの射影で分野の射影ではない — SL1）。統制軸 review:
    `backend/tests/test_stakes_ledger_guardrails.py::TestClosedWorldVocabulary.BANNED` は「この分野では未検証」等 4 語の完全一致で、
    助詞を変えた言い換えを拾わない。uxsim の審判 DB2（同じ文に「この分野」と「未検証」）は 2 人の学生の run で同じ箇所を拾った。
    構造軸 none・接続軸 none（文言 1 箇所の問題で、語彙の正本の分裂ではない）。
generalization:
  level: general
  general_form: "禁止語彙のガードレールが定型句の完全一致だけを照合するため、同じ意味の言い換え（助詞・語順の違い）が画面に入る"
pattern: wording-mismatch
discovery:
  perspective: [invariant_audit, reproduction]
  note: "学生 2 人の run で審判 B（DB2）が「この分野の未検証の前提を見る」を拾い、st-06 は出典タブの「未記帳」とこの一覧の空を並べて「画面によって言い方が違う」と記した。文言を SL1 と BANNED に照らした。 run: uxsim/runs/c-r25-doubt-r1/20261022T125348Z-41947（seq9）・uxsim/runs/c-r25-doubt-r1/20261022T125655Z-43791（seq9）。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    学習者の入口を「このコースの教材で、検証の記録がない前提を見る」に直し（app.js）、test_stakes_ledger_guardrails.py::TestClosedWorldVocabulary に「同じ文に『この分野』と未検証／検証されていない／検証記録／検証の記録が並ぶ」近接検査を足した（app.js・discuss.js・admin.js・doubt-atlas.js・routes/doubt.py・opening.py・core/doubt 全体）。manual student/02 に入口の説明を 1 段落足した。
  landed_in:
    - "frontend/public/js/app.js"
    - "backend/tests/test_stakes_ledger_guardrails.py"
    - "docs/manual/student/02-student.md"
  verification:
    methods: [guardrail]
    unverified: []
related: [IK-0853]
view_of: []
history: []
---

## 課題

症状: 学習画面の出典タブに「この分野の未検証の前提を見る」と出る。原因: 入口の文言が SL1（閉世界）に反し、ガードレールは
定型句の完全一致しか見ない。

## 発見の観点

閉世界語彙の不変条項と画面の文言の照合（invariant_audit）。審判 DB2 が 2 run で拾った（reproduction）。

## 解決の観点

解決済み（2026-10-23）。文言を閉世界・コース範囲の主語に直し、ガードレールを近接検査にする。

## 一般化

禁止語を列挙するだけのガードレールは言い換えを通す。確かめ方は、語順・助詞を変えた言い換えを入れて赤になることを見ること。
