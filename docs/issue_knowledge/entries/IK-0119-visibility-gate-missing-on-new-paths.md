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
    structure: [none]
    connection: [condition]
    governance: [review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    権限判定の仕組み自体は存在し正しく動くが、新しく足した経路がそれを呼ばずに素の集合を読む
    ため、閲覧できない教材由来の候補・名前・台帳が届く。各経路は単体では筋が通っており、
    前段で決まっている条件（この利用者が見てよい対象）が後段に渡っていないことだけが原因。
    役割の検査（教員かどうか）は通っているのに、対象の検査が抜けているという形で 3 経路に現れた。
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
---

## 課題

**症状**: ①コース構築の候補表と教材コンテキストが、本人の見られない教材まで含めて組み立てられる
②同一性候補の事実文に、閲覧できない論文の部品名が出る ③台帳の根拠の記帳が、他人の教材に対して
できる。

**原因**: いずれも役割（教員かどうか）の検査は通っているが、対象ごとの可視性・編集権限が後段の
クエリまで運ばれていない。既存の判定関数は存在するので、呼んでいないことだけが原因。

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
