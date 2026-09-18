---
id: IK-0209
title: 実行オプションが前回の実行から継承されず、既定オフの再解析が未レビューの成果を消す
status: resolved
recorded_at: 2026-07-17
resolved_at: 2026-07-18
sources:
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §2 P2
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §4
  - docs/development_checklist.md §3
feature_context:
  realizing: 解析をやり直しつつ、前回の実行条件と、まだ人がレビューしていない成果を保つ
  layers: [pipeline_a, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition, version]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 受け側の継承の分岐は書かれたとおりに正しい。
    構造: 「指定しない」を表す手段が呼び出し側に無いとも読めるが、表現は足りていて送らない選択が
    できるだけなので要素は無いと判断した。
    接続: 呼び出し側が毎回オプションを明示するため受け側の継承が一度も成立せず、実行の条件が
    前後の実行の間で伝わらない。前回の実行が残した成果に対して版がずれる面もあるので二つ置く。
    統制: 再実行の統制そのものは崩れていない。失われるものを事前に告げる確認は解決側で足した
    もので原因ではない。再実行と冪等性の問題と読む余地は残る。
generalization:
  level: general
  general_form: >-
    前回の実行条件が次の実行へ渡らず、既定値で走った再実行が前回の成果を消す
discovery:
  perspective: [boundary_walk, adversarial_review]
  note: >-
    「オプションと前回実行の継承」という境界を歩いた。継承の分岐には単体テストがあり、
    リセットの挙動も仕様として固定されていたが、既定オフのまま再解析するという操作の並びは
    誰も書いていなかった。継承の分岐は実質的に到達不能だった。
resolution:
  perspective: [carry_through, explicit_contract]
  note: >-
    オプションが指定されていないときは条件を送らないことにして、受け側の継承の分岐を初めて
    機能させた。あわせて前回の選択を画面に復元し、明示的にオフで再解析するときは失われる
    ものを事前に告げる確認を置いた（黙って消さない）。
  landed_in:
    - backend/api/routes/admin.py
    - backend/core/document_pipeline/orchestrator.py
    - frontend/public/js/admin.js
    - docs/development_checklist.md §3
related: [IK-0207, IK-0208]
view_of: []
pattern: condition-not-propagated
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.condition, connection.version]
    to: axes=processing=[none]; structure=[none]; connection=[condition, version]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 画像解析のオプションは既定でオフである。オフのまま解析をやり直すと、前回オンで
作られた図の分類や装置の候補が、まだ人のレビューを受けていない状態で警告なしに全て消える。
「情報を落とさない」という不変条項に反する。

**原因**: 受け側には「オプションが指定されていなければ前回の実行の条件を継ぐ」分岐がある。
しかし呼び出し側が毎回オプションを明示して送るため、この分岐は一度も成立しない。既定値の
オフが毎回の実行条件として確定し、オフの意味（画像を解析しない）が「前回の成果を捨てる」に
なっていた。

## 発見の観点

`boundary_walk` と `adversarial_review`。「オプション」と「前回実行の継承」の境界を歩いた。
単体テストはリセットの挙動そのものを仕様として固定していたため、テストは全て緑のままだった。
到達不能になっている分岐を疑う視点が要る。

## 解決の観点

`carry_through`（条件を実行間で運ぶ）— 呼び出し側が指定しないときは条件を送らないことで、
受け側の継承を初めて働かせた。`explicit_contract` として、明示的にオフで走らせるときには
何が失われるかを事前に告げる（黙って消さない）。

## 一般化

再実行・再処理・再デプロイのように「前回の条件を引き継ぐ」設計を持つあらゆる機能で再発する。
呼び出し側が親切のつもりで全項目を明示すると継承が死ぬ、という形をとるのが特徴。辞書の型は
`condition-not-propagated`。
