---
id: IK-0432
title: "学習チャットの出典番号 [出典N] が往復ごとに 1 から振り直され、履歴に残った前の回答の [出典2] と今回の [出典2] が別のチャンクを指していた（番号は1往復の中でだけ一意で、プロンプトに再注入される履歴とも食い違った）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が会話の前の回答に付いた出典番号を、あとの往復でも同じ資料として参照できる
  layers: [rag_chat, discuss, corpus_roaming]
classification:
  axes:
    processing: [logic]
    structure: [representation]
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
    処理・構造軸: backend/api/routes/learning.py の本体 RAG と前提知識の説明（_resolve_prerequisite_context）は
    採用した出典に `len(cited_sources) + 1` で番号を振っていた。番号は1往復の中でだけ一意で、会話（course, topic）
    の外側のキーとして履歴・プロンプト・出典チップに流用されていた（logic / representation）。接続軸: クライアントが
    履歴を {role, content} だけで送り返すと、保存（persist_chat_history は body.history を丸ごと書く）で過去の
    assistant ターンの sources が失われ、番号の対応を辿る情報が次の往復へ渡らなかった（information。medium:
    UI は sources 付きで送るので API クライアント限定）。統制軸は none。確認: 第 9 周の対話オラクルが 3 件
    （st-01 / st-04 / st-06）で、前の回答の [出典N] が後の往復で別チャンクを指すことを検出した。
generalization:
  level: general
  general_form: 1回の処理の中でだけ一意な連番を、処理をまたいで参照される識別子として使う
pattern: id-unique-only-within-inner-scope
discovery:
  perspective: [reproduction]
  note: 第 9 周（c-astro-verify-wave456）の findings（oracle A）と transcript を読み、往復ごとの sources を並べた。
resolution:
  perspective: [representation_change, carry_through]
  note: >-
    番号を (course, topic) の会話で固定する採番器 _SessionCitationNumbers を1つ置き、本体 RAG と前提知識の説明の
    両方が同じ _adopted_source_entry を通る。既に番号を持つチャンクは最初の番号を再利用し、新しいチャンクは
    これまでの最大番号の続きから振る（1往復の中では重複させない）。番号の元は保存済み履歴
    （services.load_stored_chat_history）と送られてきた履歴の assistant ターンの sources。描画メタを落として
    送り返されたターンには、本文が一致する保存済みターンのメタを戻す（_rehydrate_history_sources。一致しなければ
    推測で結ばない）。プロンプトの [出典N] ラベル・レスポンスの sources[].index・履歴の焼き込みは同じ番号。
    検索（top_k・閾値）は変えていない。
  landed_in:
    - backend/api/routes/learning.py
    - backend/api/services.py
    - backend/tests/test_ik0432_0437_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（第 9 周の対話オラクルを修正後に再実行していない）
      - 旧来の振り直しで同じ番号が別チャンクに付いた既存の履歴は、最初の対応に揃えるだけで過去の回答本文は書き換えない
related: [IK-0378]
view_of: []
history: []
---

## 課題

出典番号が往復ごとに振り直され、会話の中で同じ番号が別の資料を指す。

## 発見の観点

第 9 周の対話オラクルの指摘を、往復ごとの sources を並べて確かめた。

## 解決の観点

番号を会話単位で固定する採番器を1つにし、2つの組み立て箇所が共有する。

## 一般化

処理の内側でだけ一意な連番を、外側で参照される識別子に流用する型。
