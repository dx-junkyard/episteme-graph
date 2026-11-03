---
id: IK-1115
title: "学習チャットの developer 指示「コンテキストに十分な情報がない場合は一般的な学術知識で自然に補完」と、検証記録の閉世界の拘束文が並び、どちらが勝つかが書かれていない（検証の範囲を一般知識で補う余地が残る）"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "学習者が部品の検証の範囲を尋ねたとき、AI がこのコーパスの記録だけで答える"
  layers: [rag_chat, screen_adapter_sa]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    routes/learning.py のチューター規則 1（知識の統合）は一般知識での補完を促し、core/assistant_context/schema.py
    LEARNING_VERIFICATION_OUTPUT_CONSTRAINT（SL1）は記録の不在の言い方だけを縛る。検証の範囲そのものを一般知識で補ってよいかは書かれていない。処理軸 wording。
generalization:
  level: repo_pattern
  general_form: "同じ指示に並ぶ 2 つの規則のどちらが優先するかを書かず、出力が規則の引っ張り合いに任される"
pattern: wording-mismatch
discovery:
  perspective: [data_inspection]
  note: >-
    製品頭脳 A 4286 が req-04286 の developer で 2 規則の緊張を記した。
resolution:
  perspective: [explicit_contract]
  note: >-
    拘束文の末尾に「検証の記録（どの範囲で確かめられたか）については、一般知識で補わず、この文脈の記録だけで答えてください（一般知識で補ってよいのは概念の説明までです）」を足す。拘束文は台帳の事実が載ったときだけ system
    末尾に付く（従来どおり）。禁止語の例示はしない（ガードレール維持）。
  landed_in:
    - "backend/core/assistant_context/schema.py"
    - "backend/tests/test_round25_wave9_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "検証の範囲を問う往復で、回答が記録の外の一般知識を検証として述べないこと（第 10 周の再演）"
related: [IK-0615]
view_of: []
history: []
---

## 課題

補完の規則と閉世界の規則の優先が書かれていない。

## 発見の観点

製品頭脳 A 4286 が req-04286 の developer で 2 規則の緊張を記した。

## 解決の観点

拘束文の末尾に「検証の記録（どの範囲で確かめられたか）については、一般知識で補わず、この文脈の記録だけで答えてください（一般知識で補ってよいのは概念の説明までです）」を足す。拘束文は台帳の事実が載ったときだけ system 末尾に付く（従来どおり）。禁止語の例示はしない（ガードレール維持）。

## 一般化

同じ指示に並ぶ 2 つの規則のどちらが優先するかを書かず、出力が規則の引っ張り合いに任される。型 wording-mismatch。
