---
id: IK-0437
title: "引っかかりの候補（tension digest）・問いの帰属の候補（anchors digest）が0件のとき、応答が {course_id, items: []} だけで、学習者は「候補が無い」のか「まだ作られていない」のかを読み分けられなかった"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が候補の一覧を開いたとき、空であることの意味が分かる
  layers: [learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: backend/api/services.py の get_tension_digest / get_anchor_digest は候補ゼロでも {course_id, items: []} を
    返し、候補が会話のあとに非同期（worker）で作られるという事実を表す場所が DTO に無かった（representation。
    medium: 文言の不足 = processing.wording とも読める）。確認: 第 9 周の transcript（st-01, step 26）で
    `{"course_id": …, "items": []}` が返り、ペルソナが「候補が無いのか、まだ作られていないのか分からない」と反応した。
generalization:
  level: repo_pattern
  general_form: 非同期に作られる一覧の空を、状態の説明なしに空配列だけで返す
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: 第 9 周の transcript で digest の応答とペルソナの反応を読んだ。
resolution:
  perspective: [single_point_fix]
  note: >-
    routes/learning.py の2つの digest ルートで、items が空のときだけ facts に事実文を1つ添える
    （label_vocab.TENSION_DIGEST_EMPTY_FACT / ANCHOR_DIGEST_EMPTY_FACT。数値なし・「作られることがあります」まで）。
    items があるときは従来の形のまま。
  landed_in:
    - backend/api/routes/learning.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0432_0437_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 学習画面（app.js）は facts を描いていない（digest の空カードの表示は API の事実文だけ）
related: []
view_of: []
history: []
---

## 課題

候補ゼロの digest が空配列だけで、空の意味が読めなかった。

## 発見の観点

digest の応答と利用者の反応を並べた。

## 解決の観点

空のときだけ、非同期に作られることを含む事実文を添える。

## 一般化

非同期に作られる一覧の空を、説明なしに返す型。
