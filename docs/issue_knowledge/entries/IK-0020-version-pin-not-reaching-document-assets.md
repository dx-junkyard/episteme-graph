---
id: IK-0020
title: 版のピン留めが教材側の成果物へ伝わらず、読み手は常に最新を読む
status: open
recorded_at: 2026-09-10
resolved_at: null
sources:
  - docs/architecture/six_lenses_2026-09-10/known_issues_features.md D-11
  - docs/architecture/six_lenses_2026-09-10/known_issues_features.md D-47
  - docs/architecture/six_lenses_2026-09-10/known_issues_features.md E-13
  - docs/architecture/six_lenses_2026-09-10/known_issues_features.md F-01
feature_context:
  realizing: 共有した成果を発行版として固定し、受け取った側が所有者の更新に巻き込まれないようにする
  layers: [versioning_v, discuss, teaching_figures]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [version, condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals:
    - kind: value
      target_axis: structure
      neighbor_of: [structure.decomposition, connection.version]
      statement: 保護や固定の単位が、その対象が成り立つために必要としている依存の範囲を覆わない
      confidence: low
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 各読み取り経路は単体では正しく読める。
    構造: 固定の単位がコース側の資産に限られ、表示に必要な依存の範囲を覆えない粒度になっている。
    経路を一つずつ直しても、単位を決め直さないかぎり抜けが残る。
    接続: どの版を読むかという条件が教材側の成果物を読む経路へ通らず、読み手には最新が出る。
    統制: 誰がいつ固定するかには要素が無い。
generalization:
  level: repo_pattern
  general_form: 固定したはずの版が一部の読み取り経路にだけ効き、他の経路は最新を読む
pattern: condition-not-propagated
discovery:
  perspective: [boundary_walk, inventory]
  note: >-
    「版で固定する」ことと「その版が依存しているもの」の境界を歩いた。固定の対象は一方の
    資産に限られており、表示に必要な説明・図・解析成果は別の資産として最新を読む。四つの
    既知課題が別々の機能から同じ境界を指していた。
resolution:
  perspective: [pending]
  note: >-
    未解決。固定の範囲をどこまで広げるかが決まれば解ける。依存している資産をすべて発行版へ
    取り込む道と、読み取り経路に版の条件を通して当時の生成ログから組み立て直す道がある。
    後者は完全な再構築の負担が大きいと判断されて保留になっている。
  landed_in: []
related: [IK-0002, IK-0011]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.version, connection.condition]
    to: axes=processing=[none]; structure=[none]; connection=[version, condition]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: processing=[none]; structure=[none]; connection=[version, condition]; governance=[none]
    to: processing=[none]; structure=[decomposition]; connection=[version, condition]; governance=[none]
    reason: 軸ごとの再判定で、固定の単位が依存の範囲より狭いという粒度の要素を構造に追加した（条件を通すだけでは単位が変わらない）
---

## 課題

症状は、版を固定して読んでいるはずの受け手に、所有者が後から変えた説明や図や解析成果が
そのまま出ることである。固定の対象はコース側の資産に限られており、表示に必要な教材側の
成果物は別の資産として最新を読んでいる。

原因は、どの版を読むかという条件が一部の読み取り経路にしか通っていないことにある。それぞれの
経路は単体では正しく読む。版を固定するという保護が、依存している範囲を覆っていないだけである。

軸ごとに読み直すと、接続のほかに構造の要素がある。固定の単位が、表示に必要な依存の範囲より狭い。
条件を後段へ通す直し方を採るにせよ、どこまでを一つの版とみなすかの粒度を決めないと抜けが残る。

## 発見の観点

「版で固定する」ことと「その版が依存しているもの」の境界を意図的に歩いた。四つの別々の
機能から出た既知課題が、同じ境界を指していた。一件ずつなら「その機能の限界」に見えるが、
並べると保護の範囲の問題であることが分かる。

## 解決の観点

未解決。依存している資産をすべて発行版へ取り込む道と、読み取り経路に版の条件を通して当時の
生成ログから組み立て直す道がある。後者は再構築の負担が大きいとして保留されている。いずれに
せよ、固定の範囲を先に決めないと個別の経路を直しても抜けが残る。

## 一般化

スナップショットやピン留めを持つ仕組みすべてで再発する。固定の単位が、表示に必要な依存の
範囲より狭いときに起こる。辞書の `condition-not-propagated` に対応し、処方は「固定の範囲を
依存の範囲から決め、読み取り経路の一覧と突き合わせる」。
