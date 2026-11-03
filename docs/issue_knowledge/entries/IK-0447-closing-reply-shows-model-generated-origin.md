---
id: IK-0447
title: "お礼・締めくくりへの定型文（IK-0434）に「出典なしの AI の説明」の出所行が付いた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 出所の表示が内容の説明にだけ付く
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
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
    処理・構造軸: 定型文の往復に content_grounding="model_generated" を付けていた。UI は値があれば出所の帯を描くので、内容の説明ではない挨拶に「AI
    の一般知識（出典なし）」が付いた（wording / representation）。
generalization:
  level: general
  general_form: 内容ではない定型の応答に、内容の分類を付ける
pattern: wording-mismatch
discovery:
  perspective: [data_inspection]
  note: 第 10 周（c-astro-verify-wave456）の transcript の往復ごとの sources と mailbox の実プロンプト（req-*.json）を並べた。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    締めくくりの定型文は content_grounding を None で返し、保存の assistant_meta にも載せない（DTO は Optional。UI
    は値が無ければ出所の帯を描かない）。理解度の点数の定型文は従来どおり model_generated のまま。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0432_0437_chat_turn_fixes.py
    - backend/tests/test_ik0444_0451_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 画面での表示（app.js は値が無ければ帯を描かないことをコードで確かめたのみ）
related: [IK-0434]
view_of: []
history: []
---

## 課題

挨拶の定型文に出所の帯が付く。

## 発見の観点

第 10 周の応答と runner の出所表示を読んだ。

## 解決の観点

定型文には分類を付けない。

## 一般化

内容でない応答に内容の分類を付ける型。
