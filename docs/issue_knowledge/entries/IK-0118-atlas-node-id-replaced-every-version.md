---
id: IK-0118
title: 分野マップの節点識別子が版ごとに総取り替えになり、確定済みの位置づけが切れる
status: resolved
recorded_at: 2026-09-12
resolved_at: 2026-09-13
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12/E_concepts.md K-6
  - docs/architecture/knowledge_structure_review_2026-09-12.md §4 Phase 3 K-6 追補
  - docs/features/atlas_node_correspondence_design.md
feature_context:
  realizing: 分野の地図を改訂しながら、教員が確定した論文の位置づけを保つ
  layers: [field_atlas_s, knowledge_landscape, concept_registry]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [version]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 骨格を作り直す処理は書かれたとおり動く。構造: 節点の識別子が版ごとに作り直され、版をまたいだ対応を持てる表現がどこにも無かった。接続:
    配置とベクトルだけが版に紐づくため、地図を改訂すると前の版で確定した位置づけが現行版から参照できない。実測で 2 版間の節点識別子の重なりが 0 であることを確認した。統制:
    凍結という手続の中に対応付けの段が無かったと読む余地はあるが、原因は識別子と対応表の持ち方にある。
generalization:
  level: general
  general_form: >-
    座標系を改訂すると識別子が総取り替えになり、旧版の識別子に紐づいた人の判断が新版から
    参照できなくなる
pattern: id-not-stable-across-versions
discovery:
  perspective: [data_inspection, boundary_walk]
  note: >-
    「骨格の改訂 × 確定済み配置」という境界を歩き、2 版の節点識別子の集合を実データで突き合わせた
    （重なり 0）。同じ層の中で版非依存のキーを持つ仕組みと持たない仕組みが同居していることも見えた。
resolution:
  perspective: [representation_change, carry_through]
  note: >-
    節点の識別子を書き換える（移行）のではなく、版間の対応表を骨格の既存スロットに持ち、読み手は
    連鎖を辿って読み替えるだけにした。対応の確定は凍結の前に教員が行い、対応が付かない配置は
    旧版の行を残して現行版では事実文で示す。
  landed_in:
    - backend/core/atlas_correspondence.py
    - docs/features/atlas_node_correspondence_design.md §11.1
related: [IK-0106]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.version, structure.representation]
    to: axes=processing=[none]; structure=[representation]; connection=[version]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 分野の地図を改訂すると、前の版で教員が確認した論文の位置づけが現行版から見えなくなる。
結果として地図を育てるほど過去の判断が切れ、改訂そのものが抑止される。

**原因**: 骨格の節点識別子が版ごとに作り直される一方、配置とベクトルは版に紐づいて保存される。
別名・候補・辺の判断は版非依存のキーを持つのに、配置だけがこの規律の外にあった。

## 発見の観点

改訂と確定済み配置の境界を意図的に歩き（`boundary_walk`）、2 つの版の識別子集合を実データで
突き合わせた（`data_inspection`）。

## 解決の観点

行の識別子を書き換える移行は、過去の記録を書き換えることになるので採らなかった。対応表を
持ち、読み手が読み替える方式にした（`carry_through` + `representation_change`）。対応が付かない
ものは消さず、事実文で示す（`state_transition` の思想）。

## 一般化

座標系・分類体系・語彙の改訂は、必ず旧識別子に紐づいた人の判断を取り残す。改訂を続けたい
仕組みほど、版間の対応をどこに持つか（識別子を安定させるか、対応表を持つか）を先に決めておく
必要がある。
