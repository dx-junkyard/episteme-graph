---
id: IK-0450
title: "英語の受講者に、理解度の点数の定型文・前提確認の記録の1行・「違っていた」の事実文が日本語で返った"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 英語で学ぶ受講者に定型の応答が英語で届く
  layers: [rag_chat]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理・接続軸: 締めくくりの定型文（IK-0434）と逆質問（IK-0424）には英語版があったが、理解度の点数（IK-0397）・前提確認の記録（IK-0396）・自己確認の「違っていた」（IK-
    0399）には無かった。学習者の言語という条件が、あとから足した定型文に渡っていない（wording / condition）。
generalization:
  level: general
  general_form: ある条件（利用者の言語）を一部の経路にだけ通し、同種の後発の経路に渡さない
pattern: condition-not-propagated
discovery:
  perspective: [data_inspection]
  note: 第 10 周（c-astro-verify-wave456）の transcript の往復ごとの sources と mailbox の実プロンプト（req-*.json）を並べた。
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    言語の目印を _is_kana_kanji_free（かな・漢字を含まずラテン文字を含む）1
    つにし、締めくくり・理解度の点数・前提確認の記録（元の問いか答えのどちらかが英語）で英語の定型文を選ぶ。自己確認はリクエストに本文が無いので、保存済みの会話の最後の発話で選ぶ（読めなければ日本語）。英語の定数は
    label_vocab に足した。
  landed_in:
    - backend/api/routes/learning.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0422_prerequisite_gate_once.py
    - backend/tests/test_ik0444_0451_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 自己確認の英語は会話履歴が無いトピックでは選べない（日本語に倒れる）
related: [IK-0424, IK-0397, IK-0399]
view_of: []
history: []
---

## 課題

英語の受講者に定型文が日本語で返る。

## 発見の観点

英語ペルソナの第 10 周の報告を読んだ。

## 解決の観点

言語の条件を後発の定型文にも運ぶ。

## 一般化

条件を一部の経路にだけ通す型。
