---
id: IK-0344
title: system 導出の step に復元由来の印が無く、復元式だけに支えられたノードが理論操作グラフで source_backed に到達しうる
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §7.1
feature_context:
  realizing: 復元由来の式を導出に使いつつ、理論操作グラフで確定扱いにしない
  layers: [pipeline_a]
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
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 式の「復元由来」という条件（confidence_policy.must_not_treat_as_source_extracted）がローカル chain の step には review_reason で運ばれるのに system 導出の step には運ばれず、下流（component_graph.normalizer._is_reconstruction_backed）は step の印だけを見る。構造軸: 印を書く責務が step を作る 2 経路のうち 1 つにしか置かれていなかった（定数も agent.py 側にだけあった）。処理・統制: 要素なし。確認手段: system_derivation.py の DerivationStep 生成に review_reason が無いこと（コード）。
generalization:
  level: repo_pattern
  general_form: 同じ種類の出力を作る経路が複数あるとき、後から足した印・条件が一方の経路にだけ書かれ、下流はその印だけを見て判断する
pattern: condition-not-propagated
discovery:
  perspective: [adversarial_review, trace_walk]
  note: >-
    第 1 波で導入した「復元式は partially_source_backed 止まり」の規律を、step を生成する経路ごとに辿った。ローカル chain は印を書くが system 導出は書かず、normalizer は印しか見ない。
resolution:
  perspective: [carry_through, canonical_source]
  note: >-
    判定関数と定数を derivation_chain/schema.py と system_derivation.py に 1 つずつ置き、両経路が同じ関数で印を書く。復元は疑いではないので chain の review_reasons には積まない（J-1）。
  landed_in:
    - src/episteme_graph/agents/derivation_chain/system_derivation.py
    - src/episteme_graph/agents/derivation_chain/schema.py
  verification:
    methods: [guardrail]
    unverified:
      - 是正後の再解析による実データでの効果の実測（LLM の live 呼び出しを行わない方針のため次回の解析待ち）
related: [IK-0328]
view_of: []
history: []
---

## 課題

**症状**: 復元式だけで組まれた system 導出（solve / eliminate）のノード・辺が source_backed になりえた。

**原因**: system 導出の step 生成に review_reason が無く、印の定数・判定がローカル chain の agent.py にだけあった。

## 発見の観点

敵対的レビュー（`adversarial_review`）で規律の適用経路を 1 本ずつ辿った（`trace_walk`）。

## 解決の観点

条件を後段まで運ぶ（`carry_through`）ために判定を共有関数にし、定数の正本を schema に置く（`canonical_source`）。

## 一般化

出力を作る経路が複数ある構造では、印は「生成する型」側の共通関数で書く。経路ごとに書くと後から足した印が片方に落ちる。
