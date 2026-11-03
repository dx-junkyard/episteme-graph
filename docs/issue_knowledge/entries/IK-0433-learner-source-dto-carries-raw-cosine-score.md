---
id: IK-0433
title: "学習チャットの応答の sources[].score に類似度（cosine）の生値が載って学習者に返っていた（画面は data-score 属性に書くだけで表示しておらず、数値を見せない原則は UI の側でしか守られていなかった）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者向けの応答が数値スコアを含まず、出典の格は段階の表示だけで伝わる
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [review]
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
    構造軸: backend/api/schemas.py の SourceTierItem が `score: float` を持ち、routes/learning.py の2つの組み立て箇所が
    `round(float(r.get("score")), 3)` を入れていた。学習者向け DTO の表現に数値が居場所を持っていた（representation）。
    統制軸: 数値非表示の原則は frontend/public/js/app.js の出典ポップアップ（「類似度の生値は学習者に見せない」の注記）
    でだけ守られ、API の応答と履歴の焼き込みはガードレールに覆われていなかった（review。medium）。処理・接続は none。
    確認: 第 9 周の審判 B が 3 名分の応答で数値項目を検出し、app.js は data-score 属性に書くだけで読んだ値を使っていなかった。
generalization:
  level: repo_pattern
  general_form: 利用者に見せない値を、表示側で隠すだけで応答には載せたままにする
pattern: guardrail-does-not-cover-new-path
discovery:
  perspective: [invariant_audit]
  note: 第 9 周の審判 B（数値の項目）の指摘を、応答 DTO と app.js の読み手を突き合わせて確かめた。
resolution:
  perspective: [representation_change, guardrail_fix]
  note: >-
    SourceTierItem から score を外し（旧履歴・旧経路の "score" キーは pydantic が読み捨てる）、出典1件の組み立て
    （_adopted_source_entry）と履歴の焼き込み（_history_source_meta）にも載せない。tier の判定は
    search_chunks_with_metadata の内側で済んでいるので変えない。app.js の data-score の書き込みと読み出しを消し、
    index.html の app.js の ?v= を上げた。ガードレール: SourceTierItem に score が無いこと・応答の sources に
    score が無いこと・app.js に data-score が無いこと。
  landed_in:
    - backend/api/schemas.py
    - backend/api/routes/learning.py
    - frontend/public/js/app.js
    - frontend/public/index.html
    - backend/tests/test_ik0432_0437_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 既に保存された履歴の sources に残る "score" キーは消していない（読み出し時に DTO が読み捨てるだけ）
related: []
view_of: []
history: []
---

## 課題

学習者向けの出典 DTO に類似度の生値が載っていた。

## 発見の観点

数値を見せない原則と、応答 DTO・画面の読み手を突き合わせた。

## 解決の観点

DTO の表現から数値を外し、表示側で隠す運用に頼らない。

## 一般化

見せない値を表示側で隠すだけで、応答には載せたままにする型。
