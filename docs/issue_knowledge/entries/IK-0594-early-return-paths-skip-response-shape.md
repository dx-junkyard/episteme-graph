---
id: IK-0594
title: "学習チャットの早期 return（HELP・要素説明・地図・固定文）が応答の骨格を通らず、生成本文の問い返しが連なり shape も空になる"
status: resolved
recorded_at: 2026-10-02
resolved_at: 2026-10-02
sources:
  - docs/features/dialogue_response_shape_design.md §8.2
feature_context:
  realizing: "学習チャットの応答を「答え → 確認 → 問い返し 1 つ」の区画で返す（応答の骨格 RS1〜RS5）"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: `routes/learning.py::_learning_chat_core` の早期 return を列挙すると、`assemble` を通るのは最終 return と
    第 19 波の学習相談・前提説明（`_shape_advice_answer`）だけで、要素説明（`_generate_graph_element_explanation` の
    LLM 経路・「最後に短い確認文を1つ」の指示を LLM の自制に任せる）と HELP の音声 / casual 整形は生成本文を区画に
    通していなかった。`LearningChatResponse.shape` は最終 return だけが埋め、早期 return の 14 箇所中それ以外は None
    （構造軸 responsibility = 骨格の適用を経路ごとの書き足しに任せていた / 接続軸 contract = DTO の shape 契約が
    早期経路で満たされない）。判定・分岐の誤りではないので処理軸は none。順序・予算・判断の割り当てに関わらないので
    統制軸は none。
generalization:
  level: repo_pattern
  general_form: "応答の形の規則を主経路に置き、早期に返す分岐が同じ規則と同じ出力契約を通らない"
pattern: guardrail-does-not-cover-new-path
discovery:
  perspective: [inventory]
  note: "第 19 波の残り（IK-0586 の続き）として `_learning_chat_core` の早期 return を全件列挙し、区画から外れる経路と shape を埋めない経路を表にした。"
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    生成本文の早期経路（要素説明の LLM 経路・HELP の音声 / casual 整形）は `assemble` + `render_answer_text` で問い返しを
    1 つまでにし、保存文も同じ本文にした。本文を組み替えない経路（HELP のマニュアル本文素通し・無ヒット固定文・点数 /
    締め / 地図 mind・learn / 学習パスに戻る / 承認済み要素説明 / 前提確認済みの固定文）は新設の
    `core/dialogue_shape.py::verbatim_shape_dto`（区画を読むだけ・本文はバイト一致）で shape を埋めた。問い返しの無い
    経路に問いは足さない。早期 return を足したときに shape を書き忘れないよう、4 関数の `LearningChatResponse(` 呼び出しに
    `shape=` があることを AST で固定した。
  landed_in:
    - backend/core/dialogue_shape.py
    - backend/api/routes/learning.py
    - backend/tests/test_early_return_shape.py
    - docs/features/dialogue_response_shape_design.md
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（要素タップの説明・音声モードの使い方の質問）"
      - "ストリーミング経路（早期 return はストリーム化されない前提を確認していない）"
related: [IK-0586, IK-0582, IK-0574]
view_of: []
history: []
---

## 課題

応答の骨格（RS1〜RS5）は最終 return と学習相談・前提説明だけに掛かっていた。要素タップの説明（EXPLAIN_GRAPH_ELEMENT）は
LLM に「最後に短い確認文を1つ」と頼むだけで、問い返しが連なっても組み替えられない。HELP の音声 / casual 整形も同じ。
早期 return の `LearningChatResponse.shape` は空で、フロントは問い返しを切り分けられない。

## 発見の観点

IK-0586 の残りとして早期 return を全件列挙し、各経路が区画のどれに外れるか（問い返しが複数 / 確認が先 / 鏡が核心語以外）を
事実で並べた。確認が先・鏡のずれは早期経路には無く、外れていたのは生成本文の問い返しの数と DTO の欠落だった。

## 解決の観点

規則を経路ごとに書き足すのではなく、区画に通す（生成本文）か区画を読むだけ（固定文・マニュアル）の 2 つに分け、
全経路が shape を埋めることを構造検査で固定する。マニュアル本文は切り詰めない・言い換えない（捏造禁止）。

## 一般化

応答の形の規則を主経路に置き、早期に返す分岐が同じ規則と同じ出力契約を通らない。
