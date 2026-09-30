---
id: IK-0509
title: "前提の逆質問が同じトピックで毎ターン出て、逆質問を出したターンは元の質問に答えず、内部都合の前置きまで学習者に見せていた"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/backend/rag-chat.md
feature_context:
  realizing: "学習チャットで前提知識の確認をはさみつつ質問に答える"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [condition]
    governance: [ordering]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    逆質問の提示を progress_data.prerequisite_gates_presented に 1 コース×トピック×学習者で一度だけ記録し、ゲートのターンでも通常の RAG で答えてから PREREQUISITE_GATE_ANSWERED_MARKER 付きで逆質問を添える。前置き文は廃止。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "確認の関門が一度きりの記録を持たず、関門を出した往復で本来の依頼を処理しない"
pattern: gate-position-wrong
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 14 周（uxsim/runs/c-astro-structure-30）で学生ペルソナが同じトピックで逆質問を繰り返し受け、質問が答えられないまま往復が進んだ（findings の審判 C と mailbox の製品 prompt）。"
resolution:
  perspective: [first_class_state, single_point_fix]
  note: "逆質問の提示を progress_data.prerequisite_gates_presented に 1 コース×トピック×学習者で一度だけ記録し、ゲートのターンでも通常の RAG で答えてから PREREQUISITE_GATE_ANSWERED_MARKER 付きで逆質問を添える。前置き文は廃止。"
  landed_in:
    - backend/api/routes/learning.py
    - backend/api/services.py
    - backend/tests/test_triage14_prerequisite_gate_answers.py
    - backend/tests/test_ik0422_prerequisite_gate_once.py
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified: []
related: []
view_of: []
history:
  - date: '2026-09-30'
    field: resolution.verification
    from: guardrail のみ・砂場再演は未確認
    to: guardrail + 砂場再演
    reason: >-
      第 15 周（persona_enactment_testing_design.md §18.7）の再演で症状の消失を頭脳メモが GONE と記録した。
---

## 課題

逆質問が毎ターン出て、出したターンは元の質問に答えていなかった。「前提の確認はまだ記録していません」の前置きは内部都合の説明だった。

## 発見の観点

第 14 周で学生ペルソナが同じトピックで逆質問を繰り返し受け、質問が答えられないまま往復が進んだ（findings の審判 C と mailbox の製品 prompt）。

## 解決の観点

逆質問の提示を progress_data.prerequisite_gates_presented に 1 コース×トピック×学習者で一度だけ記録し、ゲートのターンでも通常の RAG で答えてから PREREQUISITE_GATE_ANSWERED_MARKER 付きで逆質問を添える。前置き文は廃止。

## 一般化

確認の関門が一度きりの記録を持たず、関門を出した往復で本来の依頼を処理しない。
