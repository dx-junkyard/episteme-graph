---
id: IK-0434
title: "お礼・締めくくりだけの発話（「ありがとうございました、今日はここまでにします」「Thank you, that is very helpful!」）でも検索と回答生成が走り、無関係な出典が 8 件並び、「私の暫定的な立場」を述べ、discuss は学習者が終えると言っているのに必須の問い返しで締めていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が会話を締めくくったとき、短く受け止めて終われる
  layers: [rag_chat, discuss]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [none]
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
    構造軸: 学習チャットの様相・意図の語彙（DOMAIN_RAG / LEARNING_ADVICE / USAGE_HELP / casual_light・discuss）に
    「締めくくり」の居場所が無く、内容の無い発話も質問として RAG 経路へ流れた（representation。medium）。処理軸:
    backend/api/routes/learning.py の _learning_chat_core は HELP と点数の pre-route のあと、内容語の有無を見ずに
    検索（top_k=8）と生成へ進み、discuss の生成プロンプトは末尾の問い返しを必須にしている（logic）。確認: 第 9 周の
    transcript で、お礼の往復に出典 8 件と「暫定的な立場」が返り、discuss では問い返しが付いた。
generalization:
  level: general
  general_form: 語彙に居場所の無い発話の種類を、既定の処理（質問として答える）へ流す
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: 第 9 周の transcript で、会話を締めくくった往復の応答（出典・本文末尾）を読んだ。
resolution:
  perspective: [single_point_fix]
  note: >-
    HELP と点数の pre-route の直後に、非LLM の pre-route を1つ足した（_is_closing_utterance）。定型句（日英）と
    強めるだけの語を取り除いて何も残らない発話だけを拾い（「ありがとう、でも µ はなぜ負？」は拾わない）、
    検索・生成・quota 消費なしで label_vocab.CLOSING_UTTERANCE_REPLY（かな・漢字を含まない発話は _EN）を返す。
    content_grounding は model_generated・sources は空・stance なし・問い返しなし。typed action・テキスト選択・
    理解サイクル・確認問題の壁打ちの往復は対象外。discuss も同じ位置を通る。
  landed_in:
    - backend/api/routes/learning.py
    - backend/core/label_vocab.py
    - backend/tests/test_ik0432_0437_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 定型句の一覧に無い言い回し（「では失礼いたします」等）は拾わず従来どおり答える
related: [IK-0397]
view_of: []
history: []
---

## 課題

お礼・締めくくりの発話にも検索と回答生成が走り、問い返しで終わっていた。

## 発見の観点

会話を締めくくった往復の応答を読んだ。

## 解決の観点

内容の無い締めくくりだけを決定論で拾い、固定の1文で受け止める。

## 一般化

語彙に居場所の無い発話の種類を、既定の処理へ流す型。
