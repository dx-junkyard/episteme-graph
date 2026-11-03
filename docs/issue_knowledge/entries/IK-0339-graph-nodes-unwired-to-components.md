---
id: IK-0339
title: グラフノードと部品の突合が式集合と導出 ID だけで、claim 連鎖では常に空になり detail ノードの親も辺の層も埋まらない
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
  - docs/features/graph_dialogue_review_design.md
feature_context:
  realizing: 理論操作グラフのノードから部品の要約・二層説明へ降り、層トグルで主グラフと式の詳細を行き来する
  layers: [pipeline_a, graph_review, graph_paper_layer]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 突合の条件（式集合・導出 ID）が式のある導出を前提にしており、claim 連鎖という
    前段の状態が後段の突合条件に伝わっていない。両側に claim ID があるのに使われない。
    処理軸: 突合関数は条件どおり動いており単一不良とは言い切れない（medium）。構造・統制:
    要素なし。辺の graph_layer 欠落は同じ関数群の実装漏れとして併記。
generalization:
  level: general
  general_form: 二つの構造を結ぶ突合キーが一方の経路にしか存在せず、別経路で作られた構造は結び付く材料があっても無接続になる
pattern: available-but-unwired
discovery:
  perspective: [data_inspection, trace_walk]
  note: >-
    10 本 2,600 ノードで component / explanation が全件 null、detail の parent が全件空で
    あることから normalizer の突合条件を読み、部品側に linked_claim_ids が入っていることを
    確認した。
resolution:
  perspective: [carry_through, single_point_fix]
  note: >-
    突合条件に step の claim ID と部品の linked_claim_ids の交差を足す。辺に graph_layer を
    出力する。説明行の element_type を claim / equation に広げ、部品に説明が無いときだけ
    降りる（推測で結ばない。重ならない detail はそのまま）。
  landed_in:
    - src/episteme_graph/agents/component_graph/normalizer.py
    - src/episteme_graph/agents/component_graph/schema.py
    - backend/api/routes/theory_components.py
    - backend/core/graph_paper_layer/builder.py
  verification:
    methods: [guardrail, scratch_db]
    unverified:
      - 是正後の再解析による実データでの効果の実測（LLM の live 呼び出しを行わない方針のため次回の解析待ち）
      - docker で組み上げた実機での E2E
related: [IK-0328, IK-0303]
view_of: []
history: []
---

## 課題

**症状**: ノードを開いても部品の要約・説明が出ない。`orphan_detail_node` 警告 623 件。
辺に `graph_layer` が無い。

**原因**: `_linked_components_for_step` の 3 条件がどれも式・導出 ID 依存で、claim 連鎖では
空。両側にある claim ID を突合に使っていない。

## 発見の観点

実データ（`data_inspection`）→ 突合関数の経路（`trace_walk`）。

## 解決の観点

存在する材料で結ぶ（`carry_through`）。突合の 1 箇所を直す（`single_point_fix`）。

## 一般化

突合キーは両側が必ず持つものを選ぶ。片方の経路でしか埋まらないキーだけで結ぶと、
別経路の構造は材料があっても孤立する。
