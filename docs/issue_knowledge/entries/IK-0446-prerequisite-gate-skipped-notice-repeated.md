---
id: IK-0446
title: "「前提の確認はまだ記録していません。そのまま答えます。」の1行（IK-0422）が、逆質問のあとトピックの以降のすべての回答の先頭に付いた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 前提確認の事実を1度だけ伝え、以降の回答の読みを妨げない
  layers: [rag_chat]
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
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: 添える条件は「履歴のもっと前に同じトピックの逆質問がある（gated_before）」で、逆質問が履歴に残る限り毎往復真になる。既に1行を添えたかどうかを見ていなかった（logic）。
generalization:
  level: general
  general_form: 一度伝えれば足りる事実を、成立条件が続く限り毎回繰り返す
pattern: condition-not-propagated
discovery:
  perspective: [data_inspection]
  note: 第 10 周（c-astro-verify-wave456）の transcript の往復ごとの sources と mailbox の実プロンプト（req-*.json）を並べた。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    _history_has_gate_skipped_notice が履歴の assistant
    ターンにこの1行（日本語・英語の定型文を前提名の位置で分けた形）があるかを見て、あれば添えない。逆質問を出し直さない判定は変えない。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0422_prerequisite_gate_once.py
    - backend/tests/test_ik0444_0451_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 書き直しで1行を添えた往復が切り詰められた後は、次の往復で再び1度だけ添える
related: [IK-0422]
view_of: []
history: []
---

## 課題

前提確認の1行が以降の回答に毎回付く。

## 発見の観点

同じトピックの回答の先頭を並べた。

## 解決の観点

既に伝えたかを履歴から見て1度にする。

## 一般化

一度で足りる事実を毎回繰り返す型。
