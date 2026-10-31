---
id: IK-0545
title: "回答本文の引用と引用先チャンクの本文の一致を検査していない（LLM の誤帰属が通る）"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-10-02
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "ペルソナ通し受講 第 14 周（uxsim/runs/c-astro-structure-30）（c-astro-structure-30）の場面"
  layers: [rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [review]
  axis_confidence:
    processing: low
    structure: low
    connection: low
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    seq135 で同番号が別チャンクに見えたのは LLM の誤帰属。検査が無い。 未解決のため原因の座標は暫定。
generalization:
  level: repo_pattern
  general_form: "回答本文の引用と引用先チャンクの本文の一致を検査していない（LLM の誤帰属が通る）"
pattern: failure-reported-as-success
discovery:
  perspective: [data_inspection]
  note: "第 14 周の審判 C・頭脳メモ・製品 prompt から。"
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: >-
    RAG 応答の後処理で、回答本文の逐語引用（「」『』“” "" の 8 字以上）を同じ文の中で結び付く [出典N] の
    チャンク本文と正規化部分文字列一致で突き合わせる決定論の計器を足した（非LLM・同期）。本文・保存文は
    書き換えず、一致を確認できない引用を DTO citation_check の事実として返し、学習画面の出典チップに事実文を
    1 行添え、観測に種別だけのイベント citation_quote_unmatched を残す。引用符の無い言い換えは検査しない。
  landed_in:
    - backend/core/citation_check.py
    - backend/api/routes/learning.py
    - backend/api/schemas.py
    - frontend/public/js/app.js
  verification:
    methods: [guardrail]
    unverified:
      - 実 LLM 応答・実チャンクでの誤帰属の検出（砂場の再演・ブラウザ表示は未実施）
      - 前提知識の説明（LEARNING_ADVICE）などの早期 return の経路
      - 引用符の無い言い換えによる誤帰属（逐語検査の対象外）
related: []
view_of: []
history:
  - date: '2026-10-02'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '回答本文の逐語引用と引用先チャンク本文の一致検査（core/citation_check.py）を後処理に置き、不一致を事実として DTO・画面・観測に出した（ガードレールで確認・再演は次の周）。（docs/backend/rag-chat.md 追補 2026-10-02）'
---

## 課題

回答本文の引用と引用先チャンクの本文の一致を検査していない（LLM の誤帰属が通る）。seq135 で同番号が別チャンクに見えたのは LLM の誤帰属。検査が無い。

## 発見の観点

第 14 周の運転記録から。

## 解決の観点

出典マーカーが付いていることを「裏付けがある」と読まず、引用文と引用先の本文を突き合わせる計器を置いた
（失敗を成功として報告しない）。不一致は落とさず書き換えず、事実として画面と観測に出す。

## 一般化

未確定。
