---
id: IK-0467
title: "トピックを移ると出典番号が 1 から振り直され、同じチャンク 9ca30943 が「主結果」では [出典6]、「確かめられていない点」では [出典7] になった（採番の範囲は (course, topic) の会話 — 設計どおりであることを文書に明記した）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "学習者が前のトピックの会話で見た出典番号を、別のトピックの会話でも同じ資料として参照できる"
  layers: [rag_chat, docs]
classification:
  axes:
    processing: [none]
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
    構造軸: 採番器 _SessionCitationNumbers の範囲は IK-0432 の設計で (course, topic) の会話。会話履歴の保存単位（learning_chat_history の
    (user, course, topic) 行）と学習画面の会話区画がトピックごとなので、トピックを跨いで番号が揃わないのは実装の誤りではなく範囲の定義による（representation, medium:
    学習者は前のトピックの会話を見ながら質問していた）。処理・接続の誤りは無い。
generalization:
  level: general
  general_form: "識別子の払い出し範囲が画面の区画と一致しているが、利用者は区画を跨いで識別子を参照する"
pattern: entry-scope-mismatch
discovery:
  perspective: [data_inspection]
  note: "odd / even の req 全件の出典ブロックでチャンク id → 番号を学習者ごと・トピックごとに突き合わせた。"
resolution:
  perspective: [explicit_contract]
  note: >-
    範囲を (course, topic) の会話のまま保ち、docs/backend/rag-chat.md §②
    に「トピックを跨いで番号を揃えない（跨ぐなら保存単位から変える別の判断）」と明記した。コードは変えていない。
  landed_in:
    - docs/backend/rag-chat.md
    - backend/api/routes/learning.py
  verification:
    methods: [guardrail]
    unverified:
      - "トピックを跨ぐ番号を学習者がどれだけ参照するか（砂場の 1 例のみ）"
related: [IK-0432, IK-0466]
view_of: []
history: []
---

## 課題

トピックを移ると出典番号が 1 から振り直され、同じチャンク 9ca30943 が「主結果」では [出典6]、「確かめられていない点」では [出典7] になった（採番の範囲は (course, topic) の会話 — 設計どおりであることを文書に明記した）

## 発見の観点

odd / even の req 全件の出典ブロックでチャンク id → 番号を学習者ごと・トピックごとに突き合わせた。

## 解決の観点

範囲を (course, topic) の会話のまま保ち、docs/backend/rag-chat.md §② に「トピックを跨いで番号を揃えない（跨ぐなら保存単位から変える別の判断）」と明記した。コードは変えていない。

## 一般化

識別子の払い出し範囲が画面の区画と一致しているが、利用者は区画を跨いで識別子を参照する。
