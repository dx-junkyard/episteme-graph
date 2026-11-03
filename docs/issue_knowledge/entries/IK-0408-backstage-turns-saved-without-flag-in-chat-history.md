---
id: IK-0408
title: "楽屋（backstage）で送った質問が、保存される会話履歴に楽屋の印を持たず、通常の往復と区別なく並ぶ（画面に「自分だけの記録」の表示を出す材料が無い）"
status: open
recorded_at: 2026-09-28
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が楽屋での質問を、集計に入らない自分だけの記録として区別して読める
  layers: [structure_descent, rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: 会話履歴の各メッセージは role / content / id と assistant 側の描画メタしか持てず、
    「この往復は楽屋で送った」を表す欄が無い（representation。persist_chat_history は user メッセージに
    メタを足す引数を持たない）。接続軸: 楽屋の判定（_is_backstage）は痕跡 payload の backstage: True と
    kind='backstage_question' までは運ばれるが、同じターンで保存する会話履歴へは運ばれない（information）。
    処理・統制は none。
generalization:
  level: repo_pattern
  general_form: 同じターンの判定が一方の記録（痕跡）には運ばれ、もう一方の記録（会話履歴）には運ばれない
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 8 周の学生が楽屋での質問を通常の会話欄に見て「自分だけの記録」の表示が無いと記録した。
    学習画面側で印を描こうとして、保存される履歴に印が無いことを辿って確かめた。
resolution:
  perspective: [pending]
  note: >-
    未解決。学習画面（app.js）は履歴の印があれば描けるが、印が無いので描いていない（推測で付けない）。
    印を足す場所は backend/api/routes/learning.py の persist_chat_history 呼び出し（通常 RAG 応答の保存。
    assistant_meta を渡している箇所）と backend/api/services.py の persist_chat_history（user メッセージの
    dict を組み立てる箇所。user 側のメタを受ける引数が無い）。印を足した後、画面は既存の楽屋の文言に
    揃えたラベルを user の吹き出しに添える。
  landed_in: []
related: []
view_of: []
history: []
---

## 課題

楽屋の質問が会話履歴で通常の往復と区別できない。

## 発見の観点

ペルソナの報告から、画面・保存履歴・痕跡の順に辿った。

## 解決の観点

未解決。履歴に楽屋の印を持たせる必要がある。

## 一般化

同じターンの判定が片方の記録にだけ運ばれ、もう片方では落ちる。
