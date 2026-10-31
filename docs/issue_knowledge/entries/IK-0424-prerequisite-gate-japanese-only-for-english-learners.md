---
id: IK-0424
title: "英語で問う受講者に前提確認の逆質問が日本語だけで返り、「Yes, I understand it」と英語で答えても理解の記帳がされず、少しあとに同じ逆質問が戻っていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 英語で学ぶ受講者が前提確認に英語で答え、その答えが記帳される
  layers: [rag_chat]
classification:
  axes:
    processing: [wording, input_handling]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: 逆質問の本文は日本語の定型だけで（wording）、「理解している」の判定語（services._PREREQ_ACK_PHRASES）も
    否定形も日本語だけだった。英語の「I understand」「I'm familiar with」、逆質問の直後の素の「yes」、英語の否定形
    「no / I don't / not familiar / please explain」を受理しなかった（input_handling）。接続軸: 受講者の言語という
    条件が、逆質問の組み立てと答えの判定に渡っていなかった（condition。medium: 言語は各段で発話から読み直せる）。
    構造軸・統制軸は none。確認: 第 9 周の英語の留学生ペルソナで、逆質問が日本語だけで返り、英語の「理解している」の
    あとの往復で同じ逆質問が戻った。
generalization:
  level: repo_pattern
  general_form: 利用者の言語という条件を、確認の問いと答えの判定の両方に渡さず、一つの言語の定型だけで組み立て・照合する
pattern: condition-not-propagated
discovery:
  perspective: [reproduction]
  note: 第 9 周の英語ペルソナの transcript で、逆質問とその答えの往復を読んだ。
resolution:
  perspective: [single_point_fix, carry_through]
  note: >-
    発話の大半がラテン文字なら、逆質問の末尾に英語の1文（label_vocab.PREREQUISITE_GATE_EN）を添え、逆質問を出し直さない
    往復の事実文も英語にする。services._is_explicit_prerequisite_acknowledgement に英語の肯定形と否定形を足し、逆質問の
    直後の素の「yes / はい」は check_prerequisites に定型の答えとして渡して記帳させる。英語の否定形は前提の説明へ流す。
    理解の答えに新しい問いが続いた往復は、元の質問と新しい問いを併せて答える（新しい問いを落とさない）。
  landed_in:
    - backend/api/routes/learning.py
    - backend/api/services.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0422_prerequisite_gate_once.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 前提の説明（explain 経路）の本文の言語（生成の指示は変えていない）
      - 日本語と英語以外の言語
related: [IK-0409, IK-0396]
view_of: []
history: []
---

## 課題

英語の受講者に前提確認が日本語だけで返り、英語の答えが記帳されない。

## 発見の観点

英語ペルソナの逆質問とその答えの往復を読んだ。

## 解決の観点

受講者の言語を逆質問の組み立てと答えの判定に渡す。

## 一般化

利用者の言語という条件を確認の問いと答えの判定に渡さない型。
