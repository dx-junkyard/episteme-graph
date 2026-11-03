---
id: IK-0397
title: "学習者が理解度を点数・割合で求めた発話（「点数で教えて」「my score」）に受け皿が無く、通常の LLM 経路へ流れていたため、数値を見せない原則が LLM の応答まかせになっていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習チャットが学習者の理解度について数値を出さない原則を守って答える
  layers: [rag_chat]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [none]
    governance: [assignment]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: 数値非表示の原則を守る判断の置き場が、確認問題の経路（check_review の
    denylist）にしか無く、チャット経路では生成モデルの振る舞いに委ねられていた（responsibility）。統制軸: 学習者の理解度という評価に関わる応答を AI
    が自由に書ける割り当てになっていた（assignment。medium: 実際に数値が出た回答は確認していない — §17.7 は「点数要求の受け皿」が無い事実の記録）。処理軸・接続軸は none。
generalization:
  level: repo_pattern
  general_form: 原則の遵守を一部の経路でだけ決定論で強制し、別の経路では生成モデルに委ねる
pattern: guardrail-does-not-cover-new-path
discovery:
  perspective: [reproduction, invariant_audit]
  note: 第 8 周で学習者が点数を求めた往復を、数値非表示の原則と照合した。
resolution:
  perspective: [responsibility_move, guardrail_fix]
  note: >-
    意図分類（LLM）の前の非LLM pre-route _is_understanding_score_request で拾い、固定の事実文
    label_vocab.UNDERSTANDING_SCORE_REQUEST_REPLY と content_grounding=model_generated を返す（LLM 0 回・quota
    非消費・痕跡なし）。誤爆を避けるため、日本語は「何点・何割」か直後に漢字・カタカナが続かない「点数・スコア」と自分・理解の語の両方、英語は my … score / grade me
    の形だけを拾う（「スコア関数の理解」「the main points」は拾わない）。
  landed_in:
    - backend/api/routes/learning.py
    - backend/core/label_vocab.py
    - backend/tests/test_learning_chat_wave6_turns.py
    - docs/backend/rag-chat.md
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - この判定に掛からない言い回し（「どれくらいできてる？」等）は従来どおり LLM 経路
      - LLM 経路が点数を書いた実例の確認（未観測）
related: []
view_of: []
history: []
---

## 課題

点数の要求に受け皿が無く、原則が LLM まかせだった。

## 発見の観点

数値非表示の原則との照合。

## 解決の観点

非LLM の pre-route で固定の事実文を返す。

## 一般化

原則の強制が一部の経路にしか無い型。
