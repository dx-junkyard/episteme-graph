---
id: IK-0395
title: "学習チャットは RAG 検索の問い文を当該発話だけから作るため、「はい、そう読みました。合っているんですか」のような内容語の無い相づち・追い質問では検索が 1 件も当たらず、直前まで出典のあった会話が「AI の一般知識」の回答へ縮退していた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習チャットが会話の流れの中の短い追い質問にも教材を根拠に答える
  layers: [rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 会話の段階（直前の学習者の問い＝相づちが指している内容）から検索の段階へ、問いの内容が渡っていなかった（information）。処理軸:
    検索語の組み立てが発話単独を前提にした条件で、内容語の有無を見ていなかった（logic。medium: 設計上の前提の欠落とも読める）。構造軸は
    none（履歴は同じリクエストに届いており、表現は足りていた）。統制軸は none。確認: 第 8 周で内容語の無い追い発話の往復だけ sources が 0 件になった。
generalization:
  level: repo_pattern
  general_form: 後段の処理に渡す入力を現在の断片だけから作り、断片が指している前の文脈を渡さない
pattern: context-lost-across-execution-boundary
discovery:
  perspective: [reproduction, data_inspection]
  note: 第 8 周の transcript で、直前の往復と出典件数を並べた。
resolution:
  perspective: [carry_through]
  note: >-
    _retrieval_query_for_turn（決定論・非LLM）: 発話に内容語（IK-0382 の
    _grounding_content_terms）が無いときだけ、履歴のうち内容語のある直近の学習者発話を前に足して1回だけ検索する。テキスト選択・要素タップ・チャンク指定がある往復は画面の箇所を指しているので借りない。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_learning_chat_wave6_turns.py
    - docs/backend/rag-chat.md
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（相づちの往復で出典が戻るか）
      - 直近の内容語のある発話が別の話題だったとき（話題の切り替え直後の相づちは前の話題で検索される）
related: [IK-0382]
view_of: []
history: []
---

## 課題

相づちだけの追い質問で出典が 0 件になる。

## 発見の観点

出典件数を往復ごとに並べた。

## 解決の観点

内容語が無いときだけ直前の内容語のある問いを検索語に足す。

## 一般化

断片が指す前の文脈を後段へ運ばない型。
