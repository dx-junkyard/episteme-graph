---
id: IK-0689
title: "コース目次のトピックが「鍵」表示のまま開け、読んで質問したトピックも鍵のままで、鍵の意味と進み具合の数え方が画面から読めない"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-10
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/learning_units_design.md"
feature_context:
  realizing: "学習者がコースの目次で、どこまで進んだか・目当てのトピックが開けるかを読む"
  layers: [learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [completion]
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
    確認（砂場の版）: 目次の状態は確認問題の完了だけで進み、locked は推奨の順番であって開けないことではないのに、画面は鍵の印（灰色）だけで説明しない。読んだ・質問したトピックの状態は変わらない。統制軸 completion = 「進んだ」の定義が利用者の行為と噛み合わない。作業ツリーの第 3 波で status_label（まだ順番が来ていません（開けます）等）と、会話のあったトピックを学習中にする導出を足した（未再演）。
generalization:
  level: repo_pattern
  general_form: "状態の印が禁止のように見えるのに実際は推奨で、利用者の行為を状態に反映しないため、印と挙動が食い違う"
pattern: completion-defined-by-proxy
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で学生 3 人がそろって「鍵なのに開けた」「何往復もしたのにロックのまま」と書いた。"
resolution:
  perspective: [canonical_source, carry_through]
  note: "目次の状態の表示語をサーバ（label_vocab.TOPIC_STATUS_LABELS）で解決して topics[].status_label として返し、locked は「まだ順番が来ていません（開けます）」と開けることを言う。会話・閲覧のあったトピック（services.list_learner_touched_topic_ids）は学習中に重ねる（完了は確認問題のまま）。"
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/api/services.py"
    - "backend/api/routes/learning.py"
    - "backend/api/schemas.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_round23_server_fixes.py"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "複数トピックで会話した学習者の目次（再演は 1 トピックだけ）"
      - "uxsim の審判 E7 は status を本人固有として落とすが status_label を落とさないため、学習者ごとの差を沈黙適応の仮説として誤検出しうる（ハーネス側）"
related: []
view_of: []
history: []
---

## 課題

症状: 鍵の印と挙動が食い違い、進み具合が残らない。原因: 状態の定義と表示。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

状態の語をサーバの status_label にし、会話のあったトピックを学習中に重ねた（TestTopicStatusOverlay）。第 23 周の再演で学生 3 人がそろって「まだ順番が来ていません（開けます）」と t18 の学習中を読んだ（GONE）。

## 一般化

状態の印の意味と挙動が違うと、利用者は進捗表示を信じなくなる。確かめ方は、トピックを読み質問した後に目次を開き直すこと。

## 第 25 周 面 C 第 9 周（2026-10-31・judge_atlas_r9）

**STILL（軽・語）**。st-03（run uxsim/runs/c-astro-r25-atlas-r9/20261031T015252Z-38248 seq 4）が「「まだ順番が来ていません（開けます）」は、来ていないのに開けるという言い方で少し混乱します」と記した。足跡なしの st-01 では最初のトピックの「（学習中）」が地図のいまここの無さと食い違って読まれた（IK-1073）。

## 第 10 周の再演（2026-10-31・judge_atlas_r10）

軽（STILL の材料）。目次の「まだ順番が来ていません（開けます）」は変わらず。st-01 の「（学習中）」は地図側の now_note（IK-1073 GONE）で地図の「いまここ」と別の印だと言われるようになった（run uxsim/runs/c-astro-r25-atlas-r10/20261031T053658Z-69836 seq 4〜5）。

## 第 11 周の再演（2026-10-31・judge_atlas_r11）

STILL（軽）。目次の「まだ順番が来ていません（開けます）」は不変（run uxsim/runs/c-astro-r25-atlas-r11/20261031T102103Z-2572 seq 4）。
