---
id: IK-0394
title: "学習チャットは会話履歴を LLM に再注入するとき 1 件 2000 字で素の文字数切りをしていたため、2,500〜4,000 字が常態のチューターの回答が文や数式（$…$）の途中で切れたまま次の往復の文脈に戻り、出典の quote も 80 字で数式を割っていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習チャットが直前の回答を踏まえて次の往復に答える
  layers: [rag_chat]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: window_history の切り詰めは content[:max_chars] の素の文字数切りで、段落・文・数式区間の境界を見なかった（input_handling）。接続軸:
    保存した回答（全文）から LLM に再注入する履歴へ移る段階で、上限を超えた後半が黙って落ち、どこで切れたかの印も無かった（information。medium: 上限の値そのものが別の会話型 AI
    の既定の流用で、表現の問題とも読める）。構造軸は none（履歴の表現は足りている。上限の置き場が呼び出し側の引数である点は既存の規約どおり）。統制軸は none。確認: 学習チャットの呼び出しが
    window_history(..., 20, 2000) で、第 8 周の回答長は 2000 字を超えるものが常態だった。出典の quote は _quote[:80] の素の切りで $…$
    を割っていた。
generalization:
  level: general
  general_form: 上限を超えた本文を境界を見ずに切り、切れたことも示さずに後段の入力へ渡す
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection, reproduction]
  note: 第 8 周の transcript で回答長と再注入される履歴の長さを突き合わせた。
resolution:
  perspective: [representation_change, guardrail_fix]
  note: >-
    window_history に trim_at_boundary（既定 False = 他の呼び出し元は不変）を足し、段落 → 文 → 文字数の順に切って末尾に「…」を付け、$…$
    の途中では切らない。学習チャットだけ 1 件 4000 字・trim_at_boundary=True にした。出典の quote は core.text_excerpt.excerpt(...,
    80, keep_dollar_math=True) に揃えた（前提説明の組み立て箇所も同じ）。
  landed_in:
    - backend/core/llm_worker/history.py
    - backend/api/routes/learning.py
    - backend/tests/test_learning_chat_wave6_turns.py
    - docs/backend/rag-chat.md
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（長い回答の次の往復で、モデルが前の回答の後半を踏まえるか）
      - 4000 字でも超える回答（段落境界で後半が落ちる事実は残る）
      - プロンプト全体の長さ・コストへの影響の実測
related: [IK-0378]
view_of: []
history: []
---

## 課題

長い回答が途中で切れたまま次の往復に戻り、数式も割れていた。

## 発見の観点

回答長と再注入される履歴の突き合わせ。

## 解決の観点

上限を学習チャットの実態に合わせ、切るときは境界で切って印を付ける。

## 一般化

上限超えを黙って削る型。
