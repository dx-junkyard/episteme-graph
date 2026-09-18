---
id: IK-0303
title: グラフ内で採番した識別子とデータベース行の識別子を同一視し、500・422・操作不能を招いた
status: resolved
recorded_at: 2026-08-29
resolved_at: 2026-09-01
sources:
  - docs/features/graph_dialogue_review_design.md §11.1
  - docs/features/graph_dialogue_review_design.md §13
feature_context:
  realizing: グラフのノードから、その実体である要素の承認・検討画面へ移動する
  layers: [theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [target]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「グラフの正規化器が付ける集約ノード・式ノードの識別子と、永続化された行の
    識別子が別の名前空間なのに、同じ『要素 ID』として同じ引数に載せていたこと」。
    型変換の失敗（500）や厳格な解決の拒否（422）は症状で、名前空間を区別する表現が
    無いことが原因。呼び出し側を個別に直しても、別経路で同じ取り違えが起きるため構造。
    設計書 §13 が「main / equation_detail の全ノードは行を持たない」ことを確認している。
generalization:
  level: general
  general_form: 由来の異なる名前空間の識別子を同じ型・同じ引数で扱い、解決できない参照が下流で例外になる
pattern: id-namespace-conflated
discovery:
  perspective: [symptom_report, trace_walk]
  note: >-
    「ほぼ全てのノードで『深く検討』が開けない」という症状から、画面が渡す識別子・
    保存されたグラフの識別子・永続化時の対応表の三者を辿って、どの段で名前空間が
    切り替わるかを突き合わせた。
resolution:
  perspective: [explicit_contract, fail_closed]
  note: >-
    ①受け口を「文書スコープ付きで別名前空間の識別子も解決する」入口と「厳格に行の
    識別子だけを受ける」入口に分け、②渡す側で代表要素へ解決する規則（行の識別子 →
    代表コンポーネント → 連結先の先頭）を置き、③どれにも解決できないノードでは
    ボタン自体を出さず事実文で案内する。エラー本文で隠す案は採らなかった。
  landed_in:
    - backend/api/routes/theory_components.py
    - frontend/public/js/admin-graph-review.js
    - docs/features/graph_dialogue_review_design.md §13
related: [IK-0301, IK-0304]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, connection.target]
    to: axes=processing=[none]; structure=[representation]; connection=[target]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は三つの顔で出た。①集約ノードを承認しようとすると 500。②ほぼ全てのノードで
「深く検討」が 422 になり、しかも無関係な文言（別の要素種別についての注意）が出る。
③どのノードが操作できるのかが画面から読めない。

原因は一つで、**グラフの中でだけ意味を持つ識別子**（正規化器が付ける集約ノードや
式ノードの番号）と、**永続化された行の識別子**が別の名前空間であるのに、同じ「要素の
ID」として同じ引数に載せていたことにある。行を持たない識別子が型変換に入れば例外になり、
厳格な解決器に入れば拒否される。

## 発見の観点

症状報告から出発し（`symptom_report`）、画面が渡す値・保存されたグラフの値・永続化時に
作られる対応表を 1 本の経路として辿った（`trace_walk`）。「対応表にも載らないので
グラフにはそのまま残る」という事実が、名前空間の切り替え点を示した。

## 解決の観点

名前空間ごとに受け口を分け、どちらを受けるかを入口の契約として書いた
（`explicit_contract`）。渡す側には「代表要素へ落とす」規則を置き、
落とせないノードでは操作要素そのものを出さない（`fail_closed`）。エラー文言だけを直す案は、
教員から見て「押せるのに必ず失敗するボタン」が残るため採らなかった。

## 一般化

同じ対象に複数の採番系（外部 ID・内部 UUID・生成器の連番・表示用の番号）がある場面では
必ず起きる。識別子を運ぶ型が 1 つしかないと、どの名前空間かは呼び出し規約の暗黙知になる。
新パターン `id-namespace-conflated` として辞書へ提案する。
