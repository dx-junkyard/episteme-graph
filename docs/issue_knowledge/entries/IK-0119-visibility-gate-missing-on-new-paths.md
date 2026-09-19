---
id: IK-0119
title: 新しく足した候補表・文脈組み立て・記帳の経路に可視性と編集権限のゲートが付かない
status: resolved
recorded_at: 2026-09-13
resolved_at: 2026-09-13
sources:
  - docs/features/learning_units_design.md §12.2 P2-R5
  - docs/features/concept_registry_design.md §13.3 P3-R2 / P3-R3
  - docs/features/knowledge_transfer_design.md §14.1 P4-R8
feature_context:
  realizing: 教員に候補・文脈・記帳の入口を提供しつつ、閲覧・編集できる対象だけに限る
  layers: [auth_visibility, learning_units, concept_registry]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 権限判定の仕組みそのものは存在し、呼べば正しく動く。構造:
    判定を呼ぶかどうかが呼び出し側任せで、末端のクエリで強制される置き場所になっていなかったため、経路を足すたびに忘れられる（条件が運ばれないことと同じ事実の別面という読み方も残る）。接続:
    前段で決まっている条件（この利用者が見てよい対象）が後段のクエリまで渡らず、素の集合が読まれる。3 経路とも同じ形だった。統制:
    役割の検査（教員かどうか）は通っており、確認や承認の手続そのものは崩れていない（誰が他人の教材へ記帳してよいかという割り当てと読む余地は残る）。
generalization:
  level: repo_pattern
  general_form: >-
    役割の検査は通しているが、対象ごとの可視性・編集権限が後段のクエリまで運ばれず、新設の
    経路が無条件で全体を読む
pattern: condition-not-propagated
discovery:
  perspective: [adversarial_review, boundary_walk]
  note: >-
    実装直後の敵対的レビューで「権限 × 新しい投影」の境界を 3 層それぞれについて歩いた。
    どの経路も役割の検査は通っており、対象の検査だけが抜けているという共通の形をしていた。
resolution:
  perspective: [carry_through, fail_closed]
  note: >-
    可視集合を必須の引数として後段まで運び、クエリの内側で対象を強制する。判定できないとき・
    出所が 1 件も残らないときは落として件数だけ正直に返す。相手の素性は可視性を通った経路でのみ示す。
  landed_in:
    - backend/core/course_units.py
    - backend/core/library/identity_candidates.py
    - backend/api/routes/doubt.py
related: [IK-0116]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.condition, governance.review]
    to: axes=processing=[none]; structure=[none]; connection=[condition]; governance=[review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: structure=[none]; governance=[review]
    to: structure=[responsibility]; governance=[none]
    reason: >-
      軸ごとの再判定で、判定の強制が末端に置かれていない点を構造に置き、レビューの手続そのものは崩れていないため統制の要素を外した
---

## 課題

**症状**: ①コース構築の候補表と教材コンテキストが、本人の見られない教材まで含めて組み立てられる
②同一性候補の事実文に、閲覧できない論文の部品名が出る ③台帳の根拠の記帳が、他人の教材に対して
できる。

**原因**: いずれも役割（教員かどうか）の検査は通っているが、対象ごとの可視性・編集権限が後段の
クエリまで運ばれていない。既存の判定関数は存在するので、呼んでいないことだけが原因。

**軸ごとの判断（2026-09-19）**: 構造は、判定の強制が呼び出し側任せで末端のクエリに置かれていない点を要素として置いた（経路を足すたびに忘れられる）。
統制は、役割の検査も承認の手続も成立しているため要素を外した。接続は対象ごとの条件が渡らない点。

## 発見の観点

実装直後に「権限 × 新しい投影」の境界を層ごとに歩いた（`boundary_walk` / `adversarial_review`）。
3 件とも同じ形（役割は通す・対象は見ない）だったため、経路の新設時に繰り返す型だと分かった。

## 解決の観点

判定を新しく書くのではなく、既存の可視集合を必須引数として後段まで運び、クエリの内側で強制した
（`carry_through`）。判定できないときは閉じ（`fail_closed`）、落としたことは件数として正直に返す。

## 一般化

新しい読み取り経路・新しい表示・新しい記帳を足すたびに、対象ごとの条件は運ばれ忘れる。
役割の検査が通っていることは対象の検査の代わりにならない。経路を増やしたら、条件が末端の
クエリまで届いているかを必ず歩く。
