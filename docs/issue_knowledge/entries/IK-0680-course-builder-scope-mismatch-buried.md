---
id: IK-0680
title: "コースビルダーで依頼の章立てが教材の範囲外だという事実が長い回答の中ほどに埋もれ、依頼と違うコースが登録されても下書き・登録結果にその事実が残らない"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/learning_units_design.md"
feature_context:
  realizing: "急ぐ教員が入門コースを依頼し、教材の範囲に合わせて変わった章立てをそのまま登録する"
  layers: [course_builder, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [review]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 範囲外の事実は LLM の回答本文の一部としてだけ出て（見出し 5 つの中ほど）、下書き（course_draft）や登録結果に構造化された事実として持たれない。急ぐ教員は読まずに「それでいい」で登録した（te-02 run 27823 seq 5〜8）。接続軸 information = 範囲外という事実が回答から下書き・登録へ運ばれない。統制軸 review = 依頼との差が確定の直前に提示されない。
generalization:
  level: repo_pattern
  general_form: "確定に影響する事実を自由文の中ほどにだけ置き、確定の直前の画面と確定の記録には持たせない"
pattern: gate-position-wrong
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、急ぐ教員が「レビューじゃないとか書いてあるが…それでいい」と読み飛ばし、依頼と違う章立てのコースを登録した。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core）: 回答の先頭に範囲の差を 1 行で置く規則と、下書きに「依頼と教材の範囲の差」の欄を持たせ、登録ボタンの直前に事実文で出す。"
related: []
view_of: []
history: []
---

## 課題

症状: 範囲外の事実が埋もれ、依頼と違うコースが登録される。原因（仮説）: 事実が自由文の中にしか無い。

## 発見の観点

ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。事実を構造化して確定の直前に出す。

## 一般化

確定を左右する事実が自由文の中ほどにしか無いと、読まない人はそのまま確定する。確かめ方は、範囲外の依頼で、確定の直前の画面に差の事実が出ることを見ること。
