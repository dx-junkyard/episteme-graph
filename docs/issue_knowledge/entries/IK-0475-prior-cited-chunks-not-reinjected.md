---
id: IK-0475
title: "前の往復で引用したチャンクが次の往復のコンテキストに入らず、モデルが自分の前の [出典2] と食い違うことを言った（番号は同じ意味でも本文が提示されない）"
status: open
recorded_at: 2026-09-28
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "会話の前の回答の出典番号を、あとの往復でもモデルが本文付きで参照できる"
  layers: [rag_chat]
classification:
  axes:
    processing: [unknown]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 接続軸: IK-0444
    以降、番号の対応（citation_map）は会話で保たれるが、コンテキストに入るのは当該往復の検索結果だけで、前の往復の引用チャンクの本文はモデルに渡らない（information）。処理軸 unknown:
    再注入するならプロンプト予算・出典一覧（cited_sources）・content_grounding の判定との関係を決める必要があり、どこで直すべきかを確定していない。
generalization:
  level: general
  general_form: "識別子の対応だけを保ち、識別子が指す本文を後続の処理に渡していない"
pattern: last-mile-missing
discovery:
  perspective: [data_inspection]
  note: "（仮説の段階）odd 側の頭脳のメモ（seq 23 / 77 / 89）と even 側 00018 の history の引用番号を突き合わせた。"
resolution:
  perspective: [pending]
  note: >-
    未着手。案: citation_map から直前の assistant ターンが引用したチャンクのうち当該往復の検索に無いもの（3件・1200字まで）を元の番号でコンテキストに足し、cited_sources
    には本文で引用されたときだけ載せる。出所の分類と未踏ガードの条件に影響するため、設計を切ってから直す。
  landed_in: []
related: [IK-0444, IK-0466]
view_of: []
history: []
---

## 課題

前の往復で引用したチャンクが次の往復のコンテキストに入らず、モデルが自分の前の [出典2] と食い違うことを言った（番号は同じ意味でも本文が提示されない）

## 発見の観点

（仮説の段階）odd 側の頭脳のメモ（seq 23 / 77 / 89）と even 側 00018 の history の引用番号を突き合わせた。

## 解決の観点

未着手。案: citation_map から直前の assistant ターンが引用したチャンクのうち当該往復の検索に無いもの（3件・1200字まで）を元の番号でコンテキストに足し、cited_sources には本文で引用されたときだけ載せる。出所の分類と未踏ガードの条件に影響するため、設計を切ってから直す。

## 一般化

識別子の対応だけを保ち、識別子が指す本文を後続の処理に渡していない。
