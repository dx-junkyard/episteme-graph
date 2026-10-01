---
id: IK-0583
title: "表示中トピックの教材の内容を問う短い質問で検索がゼロ件になり、回答が「出典を追えない AI の説明」に倒れて出典の往復が始められない"
status: open
recorded_at: 2026-10-01
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.9
feature_context:
  realizing: "ペルソナ通し受講 第 17 周（4 つの根の是正後の再演・c-astro-structure-30）の場面"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 第 17 周 s-source-roundtrip（t8・DTD の論文）で「3つに分かれるという根拠はどこ？一言で。」に対し、製品の要求（mailbox）に教材からのコンテキストが一件も無く未踏ガードが立ち、応答の sources が空だった。回答の中身は表示中トピックの教材と一致しており、表示中トピックの教材チャンクが検索の母集合か閾値で落ちている疑い（接続軸 target = 表示中トピックという対象が検索に渡っていない）。同じ場面で直前と同じ内容の要約の出所が「教材に基づく」から「出典を追えない AI の説明」に反転した。検索の母集合・スコア・トピック教材の注入経路を砂場 DB で確かめれば確定する。
generalization:
  level: repo_pattern
  general_form: "画面に表示している資料を、問いの文面だけで引く検索の結果に依存させ、表示中であることを検索の条件にしない"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 17 周のペルソナの頭脳メモ（出典チップが出ず往復が始められない）と、製品頭脳の受けた要求（教材コンテキストなし・未踏ガード）を突き合わせた。"
resolution:
  perspective: [pending]
  note: "未解決。第 17 周の記録（§18.9）で起票した。"
  landed_in: []
related: [IK-0511, IK-0382, IK-0580]
view_of: []
history: []
---

## 課題

t8 を開いた学習者が教材の内容をそのまま尋ねても、検索がゼロ件になり、回答の先頭に「出典が提示できない参考情報」の警告が付き、出典チップが出ない。出典本文を開く・紐づく主張を見るという往復の起点が立たない。

## 発見の観点

第 17 周のペルソナの頭脳メモ（出典チップが出ず往復が始められない）と、製品頭脳の受けた要求（教材コンテキストなし・未踏ガード）を突き合わせた。

## 解決の観点

未解決。原因は仮説で、classification.basis に確かめる点を書いた。

## 一般化

画面に表示している資料を、問いの文面だけで引く検索の結果に依存させ、表示中であることを検索の条件にしない。
