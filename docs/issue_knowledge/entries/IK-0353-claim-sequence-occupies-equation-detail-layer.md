---
id: IK-0353
title: claim チェーン（章内の主張の出現順）が導出チェーンと同じ顔で「式の詳細」層に入るため、式の層が無い論文では詳細層が主張の列で数百ノードに膨れ、層の名前とも中身が合わない
status: open
recorded_at: 2026-09-23
resolved_at: null
sources:
  - docs/features/theory_module_layer_design.md §3.1
  - docs/features/theory_module_layer_design.md §7
feature_context:
  realizing: グラフレビューで理論操作グラフの式の手順を層ごとに見て、理論の構造を読み取る
  layers: [pipeline_a, theory_artifacts, graph_review, graph_paper_layer]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: グラフの層の語彙（main / equation_detail / debug）に「式の操作の手順」と「章内の
    主張の並び」を区別する値が無く、正規化も導出チェーンの種別（chain_type）を見ずに全 step を
    equation_detail の EquationOperationNode にする（representation）。確認手段:
    component_graph の normalizer に chain_type の参照が無いこと（input_builder が値を渡すだけ）、
    2609.15375v1 の採用 run で詳細ノード 470 がすべて claim_chain 由来だったこと。接続軸:
    claim チェーンは前の主張を次の step の input_claim_ids / required_claim_ids に置くため、
    論文中の順序が下流では依存として読まれる（meaning。derivation_chain agent の
    _build_claim_chains）。依存の捏造そのものは IK-0354 側の原因として分け、ここでは層の意味の
    ずれに絞ったので medium。処理軸: 各関数は仕様どおりに動いているので none（順序を入力に置く
    処理を logic と読む余地があり medium）。統制軸: 順序・予算・担当の統制は関係しないので none。
generalization:
  level: repo_pattern
  general_form: 性質の異なる二種類の要素を同じ種別名の器に入れ、器の名前が片方の意味しか表さないため、もう片方が器の意味で読まれる
pattern: same-name-different-referents
discovery:
  perspective: [data_inspection, trace_walk]
  note: >-
    主グラフ 4・式の詳細 470 という層の間の大きな落差を実データで確かめ、詳細ノードの導出元を
    derivation_chain の chain_type まで辿ると全件が claim_chain だった。生成側は章ごとに主張を
    出現順に並べるだけで、正規化側は種別を区別していなかった。
resolution:
  perspective: [pending]
  note: >-
    未解決。設計書 §7 で (a-読) / (a-A) / (b-読) / (b-A) / (c) を比較し、推奨は (b-読)
    （A層非改変のまま、グラフレビューのキャンバスから claim チェーン由来の詳細ノードを外し、
    主張の並びは論文層の章アウトラインで見る）。オーナー判断 O-2 待ち。
  landed_in: []
related: [IK-0354, IK-0339, IK-0017]
view_of: []
history: []
---

## 課題

**症状**: グラフレビューの層トグルで「式の詳細」を開くと、式の層が再現されていない論文
（PDF 経路）では数百ノードの一直線の列が並ぶ。2609.15375v1 では 470 ノード・連結成分 15・
分岐ゼロで、層の名前は「式の詳細」なのに中身は主張の列だった。

**原因**: derivation_chain は、使える式が 1 本も無いとき（および 2026-09-19 以降は式チェーンが
覆わない章で）、章ごとに主張を出現順に並べた claim チェーンを作る。component_graph の正規化は
chain_type を区別しないため、claim チェーンの step も式の操作 step と同じ
`graph_layer="equation_detail"` のノードになる。層の語彙に両者を区別する値が無い。

## 発見の観点

実データの読み（`data_inspection`）で層の間の落差を確かめ、詳細ノードから導出チェーンの種別まで
経路を辿った（`trace_walk`）。

## 解決の観点

未解決。設計書 §7.3 の推奨は、A層を触らずに画面の射影で主張の並びをキャンバスから外し、既に
章ごとに主張を並べている論文層を受け皿にすること。層の語彙を A層で増やす案（(a-A)）は、
`graph_layer` を読む約 10 箇所の追随が要るので後戻りしにくい。

## 一般化

性質の異なる要素（依存を表す手順と、順序を表す並び）を同じ器に入れ、器の名前が片方の意味しか
表さないと、もう片方も器の意味（依存）で読まれる。辞書の `same-name-different-referents` は
名前の衝突として畳む型で、本件は層の名前が二種類の実体を指す形に当たる。器を分けるか、片方を
器から出すかのどちらかでしか解けない。
