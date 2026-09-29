---
id: IK-0474
title: "「ありがとうございました。ゼミではこの前提のことを話してみます。」に「前提」のマーカーで tension_hint が立ち、引っかかり抽出のプロンプトの例の turn id（msg_0010 / msg_0011）が実会話の id と衝突した"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "学習者の「理解した上での引っかかり」の候補だけを引っかかり抽出に渡す"
  layers: [learner_experience_b]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [meaning]
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
    処理軸: prefilter はヘッジ語（前提・そもそも）の部分一致を最優先し、お礼で閉じた発話かを見ていなかった（input_handling）。接続軸: few-shot の例の turn id が
    msg_{index:04d} と同じ形で、実会話の msg_0010 / msg_0011 と同じ名前が別の発話を指した（meaning）。
generalization:
  level: general
  general_form: "例示に使った識別子が実データの識別子と同じ名前空間にあり、モデルが例と実データを取り違えうる"
pattern: id-namespace-conflated
discovery:
  perspective: [data_inspection]
  note: "odd 側 req-00069 の引っかかり抽出プロンプトの Session と Worked examples を並べた。"
resolution:
  perspective: [single_point_fix]
  note: >-
    prefilter はお礼を含み問いの形（？ / ?）を持たない発話にヒントを立てない（マーカー判定より前）。例の turn id を example_a_1 形式に変え、例の id は Session
    に現れないと明記した。
  landed_in:
    - backend/core/tension/prefilter.py
    - backend/core/tension/prompt.py
    - backend/tests/test_ik0466_0478_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演"
      - "「ありがとう、でも前提が…」のように問い符号の無い逆接つきのお礼はヒントが立たない（狭めた側の取りこぼし）"
related: [IK-0470]
view_of: []
history: []
---

## 課題

「ありがとうございました。ゼミではこの前提のことを話してみます。」に「前提」のマーカーで tension_hint が立ち、引っかかり抽出のプロンプトの例の turn id（msg_0010 / msg_0011）が実会話の id と衝突した

## 発見の観点

odd 側 req-00069 の引っかかり抽出プロンプトの Session と Worked examples を並べた。

## 解決の観点

prefilter はお礼を含み問いの形（？ / ?）を持たない発話にヒントを立てない（マーカー判定より前）。例の turn id を example_a_1 形式に変え、例の id は Session に現れないと明記した。

## 一般化

例示に使った識別子が実データの識別子と同じ名前空間にあり、モデルが例と実データを取り違えうる。
