---
id: IK-0476
title: "非LLM の一次判定（prejudge）が、分野未指定のコースの明らかな内容の問い 9 件をどれも DOMAIN_RAG と決められず、すべて意図分類の LLM に回った"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "明らかな教材内容の問いを LLM を使わずに内容の問いとして扱う（LC5）"
  layers: [rag_chat]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [information]
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
    処理軸: 内容語は分野非依存の日本語の学問一般語と cartridge の語だけで、cartridge_id が空のコース・英語の問いでは内容語が1つも当たらなかった（input_handling）。接続軸
    medium: コースのトピック題名・概念名は course_data にあるが一次判定に渡っていなかった（information）。
generalization:
  level: general
  general_form: "判定の語彙を外部の辞書からだけ供給し、手元にある対象そのものの語を渡していない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "odd 側の頭脳のメモで、intent 分類の req の質問文を数えた。"
resolution:
  perspective: [carry_through]
  note: >-
    _course_content_terms(course_data) がトピック題名（区切り記号で割った3文字以上の断片）と概念名を内容語として渡し、_CONTENT_QUESTION_TERMS
    に英語の学問一般語を足す（分野語は書かない）。heuristic 本体は不変で、LLM 回数は増えない（LC5）。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0466_0478_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（手元の再現では第 11 周の英語の問いの一部だけが DOMAIN_RAG に決まり、残りは従来どおり LLM 分類）"
related: []
view_of: []
history: []
---

## 課題

非LLM の一次判定（prejudge）が、分野未指定のコースの明らかな内容の問い 9 件をどれも DOMAIN_RAG と決められず、すべて意図分類の LLM に回った

## 発見の観点

odd 側の頭脳のメモで、intent 分類の req の質問文を数えた。

## 解決の観点

_course_content_terms(course_data) がトピック題名（区切り記号で割った3文字以上の断片）と概念名を内容語として渡し、_CONTENT_QUESTION_TERMS に英語の学問一般語を足す（分野語は書かない）。heuristic 本体は不変で、LLM 回数は増えない（LC5）。

## 一般化

判定の語彙を外部の辞書からだけ供給し、手元にある対象そのものの語を渡していない。
