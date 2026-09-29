---
id: IK-0396
title: "前提確認の逆質問に「はい、理解しています」と答えると、理解の記帳はされるが、逆質問を引き起こした元の質問には答えないまま「はい、理解しています」という発話そのものへの応答が返り、学習者は同じ質問を打ち直していた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習チャットが前提確認を挟んだあとに学習者の元の質問へ答える
  layers: [rag_chat]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 逆質問（介入）で止めた往復の元の質問が、次の往復（はい/いいえの答え）に渡っていなかった（information）。構造軸:
    「前提確認の答えを待っている元の質問」を表す居場所が無く、ゲートを通過した後に再処理する対象が無かった（representation。medium:
    状態を持たずに履歴から読む解決を採ったので、表現の欠落というより参照の欠落とも読める）。処理軸は none（記帳の判定は正しかった。medium）。統制軸は none。確認: 第 8 周で逆質問
    →「はい、理解しています」→ 一般的な相づちへの応答、の往復があった。
generalization:
  level: repo_pattern
  general_form: 処理を止めて確認を挟むゲートが、確認が済んだあとに止めた処理を再開する経路を持たない
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: 第 8 周の transcript で、逆質問の前後の往復を読んだ。
resolution:
  perspective: [carry_through]
  note: >-
    記帳（check_prerequisites）はそのまま、直前が逆質問で本人が明示的に理解していると答えた往復だけ、履歴で逆質問の直前にある学習者発話（_question_before_prerequisite_gate）を問いとして同じリクエストで通常の
    RAG 経路に通す。回答の LLM は1回・意図分類は追加しない。回答の先頭に label_vocab.PREREQUISITE_ACK_RESUME_NOTICE
    を添え、保存する学習者発話は本人が打った文のまま。「はい、理解しています」ボタン（typed action continue_detail）でも同じ。
  landed_in:
    - backend/api/routes/learning.py
    - backend/core/label_vocab.py
    - backend/tests/test_learning_chat_wave6_turns.py
    - docs/backend/rag-chat.md
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 元の質問と逆質問の間に別の往復が挟まったとき（履歴の直前だけを見るので再開しない）
related: [IK-0383]
view_of: []
history: []
---

## 課題

前提確認のあと元の質問に答えない。

## 発見の観点

逆質問の前後の往復を読んだ。

## 解決の観点

履歴から元の質問を取り戻し、同じ往復で答える。

## 一般化

確認を挟むゲートが再開経路を持たない型。
