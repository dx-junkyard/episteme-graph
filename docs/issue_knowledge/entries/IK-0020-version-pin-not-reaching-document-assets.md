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
    structure: [none]
    connection: [version, condition]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「どの版を読むかという条件が、コース側の読み取り経路には通っているのに、教材側の
    成果物を読む経路へは通っていない」こと。各経路は単体では正しく読めており、表現にも
    欠けが無い。条件を後段まで運ばないかぎり、版を増やしても読み手には最新が出る。確認手段は
    四つの既知課題が指す読み取り経路で、いずれも最新を読む実装であること。
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
---

## 課題

症状は、版を固定して読んでいるはずの受け手に、所有者が後から変えた説明や図や解析成果が
そのまま出ることである。固定の対象はコース側の資産に限られており、表示に必要な教材側の
成果物は別の資産として最新を読んでいる。

原因は、どの版を読むかという条件が一部の読み取り経路にしか通っていないことにある。それぞれの
経路は単体では正しく読む。版を固定するという保護が、依存している範囲を覆っていないだけである。

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
