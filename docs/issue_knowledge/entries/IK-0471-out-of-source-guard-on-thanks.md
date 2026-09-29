---
id: IK-0471
title: "お礼の発話（CHIT_CHAT → casual_light、出典0件）にも未踏ガードが注入され、挨拶の返答に「教材で確かめる対象はありません」のようなガード由来の説明が出た"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "教材に根拠が無いことの正直な明示を、教材で確かめる内容の問いにだけ付ける"
  layers: [rag_chat, discuss]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [condition]
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
    処理・接続軸: ガードの付与条件は content_grounding == model_generated
    だけで、発話が内容の問いかを見ていなかった。「ありがとうございました。これでゼミで説明できそうです。」は定型句の後に一言が続くので IK-0434 の締めくくり pre-route に当たらず、検索0件 →
    model_generated → ガード注入となった（condition）。
generalization:
  level: general
  general_form: "出所の注意の付与条件が根拠の有無だけで、そもそも根拠を問う発話かどうかを見ていない"
pattern: condition-not-propagated
discovery:
  perspective: [data_inspection]
  note: "req-00040 の system プロンプト末尾と、req-00069 の窓に入った discuss のお礼の返答を読んだ。"
resolution:
  perspective: [single_point_fix]
  note: >-
    ガードと注意書きの付与を「model_generated かつ intent != CHIT_CHAT かつ _is_closing_led_statement でない」に絞る（content_grounding
    の判定は変えない）。_is_closing_led_statement はお礼・締めくくりの定型句を含み問いの形でない 160 字以下の発話。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0466_0478_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演"
related: [IK-0378, IK-0434, IK-0473]
view_of: []
history: []
---

## 課題

お礼の発話（CHIT_CHAT → casual_light、出典0件）にも未踏ガードが注入され、挨拶の返答に「教材で確かめる対象はありません」のようなガード由来の説明が出た

## 発見の観点

req-00040 の system プロンプト末尾と、req-00069 の窓に入った discuss のお礼の返答を読んだ。

## 解決の観点

ガードと注意書きの付与を「model_generated かつ intent != CHIT_CHAT かつ _is_closing_led_statement でない」に絞る（content_grounding の判定は変えない）。_is_closing_led_statement はお礼・締めくくりの定型句を含み問いの形でない 160 字以下の発話。

## 一般化

出所の注意の付与条件が根拠の有無だけで、そもそも根拠を問う発話かどうかを見ていない。
