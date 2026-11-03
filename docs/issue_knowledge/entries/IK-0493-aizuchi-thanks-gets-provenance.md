---
id: IK-0493
title: "「なるほど、ありがとうございます。」へのお礼の返答に「出所: 出典を追えない AI の説明」が付いた（IK-0447 の締めくくりの判定に相づちが入っていなかった）"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17.10
feature_context:
  realizing: 出所の表示が内容の説明にだけ付く
  layers: [rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: IK-0447 は締めくくりの pre-route（_is_closing_utterance = 定型句と強めの語だけの発話）の返答だけ content_grounding=None にしていた。
    相づち「なるほど」「わかりました」は強めの語の表に無く、「なるほど、ありがとうございます。」（第 12 周 c-astro-verify 98728 の transcript）は pre-route を外れて
    RAG へ進み、採用した根拠が無いため model_generated が付いた。お礼で始まる一言付きの発話（_is_closing_led_statement）も同じ経路で model_generated に
    なっていた（logic）。構造軸 none: 出所の語彙は足りている。
generalization:
  level: general
  general_form: 内容ではない定型の応答かどうかを、狭い語彙表だけで判定し、外れた発話に内容の分類を付ける
pattern: wording-mismatch
discovery:
  perspective: [data_inspection]
  note: "第 12 周の transcript で「ありがと」を含む往復の args.message と content_grounding を並べた。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    ①締めくくりの強めの語に相づち（なるほど・わかりました・了解しました・承知しました・はい / i see・got it・understood 等）を足す（定型句が無い発話は
    従来どおり拾わない）②お礼で始まる発話（_is_closing_led_statement）への RAG 返答が本文で何も引用していなければ content_grounding を None にする
    （応答・保存の両方）。内容の問いの model_generated は不変。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0492_0496_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演"
      - "画面の出所の帯（app.js は値が無ければ帯を描かないことを IK-0447 でコード確認したのみ）"
related: [IK-0447, IK-0471, IK-0434]
view_of: []
history: []
---

## 課題

相づち付きのお礼に「出典を追えない AI の説明」の出所行が付いた。

## 発見の観点

第 12 周の transcript の往復ごとの発話と出所の値を並べた。

## 解決の観点

相づちを締めくくりの判定に入れ、お礼で始まる発話への引用なしの返答には出所の分類を付けない。

## 一般化

内容ではない定型の応答かどうかを狭い語彙表だけで判定し、外れた発話に内容の分類を付ける。
