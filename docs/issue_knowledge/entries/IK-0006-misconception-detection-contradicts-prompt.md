---
id: IK-0006
title: 誤解検出が決まった語の出現に依存する一方、生成側にはその語を避けるよう指示している
status: open
recorded_at: 2026-09-10
resolved_at: null
sources:
  - docs/architecture/six_lenses_2026-09-10/known_issues_architecture.md G-07
  - docs/architecture/six_lenses_2026-09-10/01_learner.md §2 提案3
feature_context:
  realizing: 対話の中で訂正が起きたことを検出し、後で振り返れる記録にする
  layers: [rag_chat]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 検出処理も生成の指示もそれぞれ単体では意図どおりで、前提が妥当という条件を満たさない。
    表層の語を足す直しでは解けないため、単一処理の不良としては数えない。
    構造: 出力の形式についての取り決めが、生成する側と読み取る側に分かれて置かれている。
    接続: 生成側の指示と検出側の前提が両立しない。
    統制: 誰が判断するか・順序・予算のいずれにも要素は見当たらない。
generalization:
  level: general
  general_form: 生成する側と読み取る側の取り決めが別々に変更され、互いの前提が食い違う
pattern: contract-changed-one-side
discovery:
  perspective: [doc_code_diff, trace_walk]
  note: >-
    生成の指示文と、その出力を読む検出処理を並べて読んだときに見えた。指示は決まった
    言い回しを避けるよう書き、検出はその言い回しの出現を手がかりにしている。指示に忠実な
    応答ほど検出から漏れる。
resolution:
  perspective: [pending]
  note: >-
    未解決。どちらを正本にするかが決まれば解ける。生成側に機械可読の目印を出させて検出を
    その目印に寄せるか、検出をやめて訂正の記録自体を本人の操作に寄せるかの二択が見えている。
    表層の語を足す対症は、指示が変わるたびに外れるので採らない。
  landed_in: []
related: [IK-0005, IK-0019]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.contract, local.logic]
    to: axes=processing=[logic]; structure=[none]; connection=[contract]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: processing=[logic]; structure=[none]; connection=[contract]; governance=[none]
    to: processing=[none]; structure=[aggregation]; connection=[contract]; governance=[none]
    reason: 軸ごとの再判定で、前提が両立しない以上「入力・契約・前提が妥当」という処理軸の条件を満たさないと見て処理の値を外し、取り決めが二箇所に分かれて置かれている点を構造に置いた
---

## 課題

症状は、訂正が起きているのに記録が残らない場合と、逆に訂正でない発話が拾われる場合が混在する
ことである。検出は決まった語の出現を手がかりにしているが、生成側の指示文はまさにその語を
避けるよう書かれている。指示に忠実な応答ほど検出から漏れる。

原因は、生成側の取り決めと検出側の前提が別々に決まっていて両立しないことにある。どちらの処理も
単体では意図どおり動く。互いの前提が噛み合っていないだけである。

軸ごとに読み直すと、処理軸は「入力・契約・前提がすべて妥当」であることを前提にした軸なので、
前提そのものが両立していないこの課題には当たらない。代わりに、出力の形式についての取り決めが
生成側と読み取り側の二箇所に分かれて置かれている点を構造軸に置いた。

## 発見の観点

生成の指示文と、その出力を読む処理を並べて読んだ。片方が避けろと言う語を、もう片方が唯一の
手がかりにしている。書かれている場所が離れているため、それぞれを単体で読むかぎり気づけない。

## 解決の観点

未解決。検出語を足す対症は、指示文が変わるたびに再び外れるので採らない。生成側に機械可読の
目印を出させて検出をそこへ寄せるか、そもそも訂正の記録を本人の操作にするかのどちらかを選べば
解ける。どちらを選ぶかは、記録の主体を誰にするかという別の問いに従属する。

## 一般化

出力の形式を一方が決め、他方がその形式を前提に読む構成すべてで再発する。片側だけを変更した
瞬間に静かに壊れ、例外も警告も出ない。辞書の `contract-changed-one-side` に対応し、処方は
「読み取り側が依存している形式を、生成側の取り決めと同じ場所に置く」。
