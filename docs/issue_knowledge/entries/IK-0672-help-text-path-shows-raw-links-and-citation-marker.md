---
id: IK-0672
title: "使い方の質問への回答（マニュアル本文の素通し）に「[書き直し・以降の削除](#edit-delete-message)」のリンク記法と、対応の無い「[出典1]」がそのまま出る"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/manual_help_kb_design.md"
feature_context:
  realizing: "学習者が学習チャットで使い方（記録がどこに残るか・消せるか）を尋ね、マニュアルの回答を読む"
  layers: [help_kb, frontend_learning_ui, rag_chat]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [contract]
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
    確認: テキスト経路の HELP 応答は本文の後に固定で「[出典1]」を書く（routes/learning.py）が、応答に sources が無いので app.js の linkifyCitations は素通しにし、記号のまま出る。マニュアル本文の Markdown リンクも renderAiContent が扱わない（太字・コードだけ）ので記法のまま出る。接続軸 contract = サーバが書く記号と画面が解ける記号が一致しない。処理軸 wording。
generalization:
  level: repo_pattern
  general_form: "送る側が受け取る側の解けない記法を本文に埋め込み、受け取る側がそれを記号のまま見せる"
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、開示を確かめた学生が「リンクが (#edit-delete-message) 等の生 anchor で見える・[出典1] が宙に浮く」と書いた。"
resolution:
  perspective: [pending]
  note: "未解決。推奨（core）: テキスト経路では「[出典1]」を書かず（マニュアル出典チップが別にある）、マニュアルの決定論変換でリンクを見出し名の文に落とす。"
related: [IK-0430]
view_of: []
history: []
---

## 課題

症状: 使い方の回答にリンク記法と対応の無い出典番号が記号のまま出る。原因: サーバの本文と画面の描画規則の契約が合っていない。

## 発見の観点

ペルソナの通し受講（reproduction）。

追記（第 23 周 st-05・開示を確かめる場面）: 使い方の答え（LLM 0 回）の本文末尾に「[出典1]」が残るが、応答の sources は空で、マニュアルの出所は別キー（manual_citations）にある。画面が本文の [出典N] を sources から引くなら対応の無い番号になる。答えの内容（取り消し・持ち出し・教員の一覧に残ること）は入力欄の上の事実文と一致していた。

## 解決の観点

未解決。本文側（マニュアルの決定論変換）で画面が解けない記法を残さない。

## 一般化

受け取り側が解けない記法を送ると、記号が利用者に見える。確かめ方は、送る本文に含まれる記法の集合と、画面の描画規則が扱う記法の集合を突き合わせること。
