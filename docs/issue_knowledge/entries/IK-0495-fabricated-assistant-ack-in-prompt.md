---
id: IK-0495
title: "学習チャットのプロンプトに、モデルが言っていない assistant ターン「はい、「…」についてですね。お答えします。」が毎回入っていた（英語の会話にも日本語で）"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17.10
feature_context:
  realizing: "学習チャットに渡す会話が、実際の会話と指示だけで組み立てられる"
  layers: [rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: routes/learning.py の足場は system → user（コンテキスト + 指示）→ assistant（_scaffold_assistant_ack の固定文）の3件で、assistant ターンを
    応答の受け止め役として作り話で置いていた（representation — 指示と会話の区別が表現されていない）。処理軸: tutor の分岐では「お答えします」の Q&A
    フレームを毎往復再導入していた（第 12 周の実プロンプト 74 件中 39 件）。接続軸 low: 会話の言語（英語セッション）が固定文に伝わらず、日本語の
    assistant 発話が英語の会話に混ざった（condition）。
generalization:
  level: general
  general_form: "指示を相手に受け止めさせるために、相手が言っていない発話を会話の中に作って置く"
pattern: condition-not-propagated
discovery:
  perspective: [data_inspection]
  note: "第 12 周の実プロンプト（req-*.json）の messages[2] を数えた。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: >-
    assistant ターンを作らない。応じ方の指示（壁打ちモードの「答えの組み立ては学習者に委ね」、discuss の「発話のタイプを見きわめて」）は足場の user
    ターンの指示文に含めた。足場の直後は実際の会話履歴か今回の発話になる（user が連続し得る。OpenAI は受け付け、Gemini 変換は role をそのまま並べる）。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_discuss_mode.py
    - backend/tests/test_learning_chat_infra.py
    - backend/tests/test_ik0492_0496_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（回答の質・書き出しの変化は見ていない）"
      - "Gemini 系プロバイダでの user ターン連続の受け付け（実 API は呼んでいない）"
      - "足場の指示文そのものは日本語のまま（英語の会話で日本語の指示を読ませる点は残る）"
related: [IK-0473]
view_of: []
history: []
---

## 課題

プロンプトに作り話の assistant ターンが毎回入っていた。

## 発見の観点

実プロンプトの messages[2] を数えた。

## 解決の観点

assistant ターンを作らず、応じ方の指示は user ターンの指示文に含めた。

## 一般化

指示を受け止めさせるために、相手が言っていない発話を会話に作って置く。
