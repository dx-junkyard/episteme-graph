---
id: IK-0844
title: "主グラフの theory stage 集約が論文の境界を潰す — 消去・基盤の段に論文のほぼ全式が入り、段の間に往復辺があって読む順が決まらず、中心命題の到達点に当たる段が無い"
status: open
recorded_at: 2026-10-22
resolved_at: null
sources:
  - "docs/pipeline/theory-graph.md"
  - "docs/features/graph_dialogue_review_design.md"
feature_context:
  realizing: "理論操作グラフの主グラフ（#308 の theory stage 集約）を、論文の議論を読む順のバックボーンとして見せる"
  layers: [pipeline_a, graph_review]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [unknown]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: main ノードは stage 単位で全 derivation を跨いで集約されるため、成員が和集合（消去 76 式・基盤 41 式）になり、
    段の間の辺は成員どうしの辺の向きをそのまま持ち上げるので往復辺（消去 → 基盤 → 消去）が生じる。到達点（中心命題の
    結果）に当たる段は stage 語彙に無い。構造軸 aggregation = 集約の単位が論文の境界と一致しない。接続軸 unknown =
    部品層（component_assembly）・thesis の支持構造が集約に渡っていないかは未確認。IK-0753 から、材料（内部 ID・件数）の
    是正と切り分けて残した部分。確定には normalizer の stage 集約と main 辺の向きの決め方を読む。
generalization:
  level: general
  general_form: "固定語彙の段に全体を集約すると、成員の和集合と辺の持ち上げしか残らず、読む順と到達点が表せない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "IK-0753 の是正（narrative_annotator の材料を人向けに組み直す）の際に、同エントリの症状のうち段の境界・往復辺・到達点の欠如は材料ではなく集約そのものの問題として残ることを確認して切り分けた（出所: IK-0753 が引く uxsim/runs/radar_loop/BRAIN_NOTES_M1.md・Q1・T1・W2・X2・Z1）。"
resolution:
  perspective: [pending]
  note: "未解決。段の成員が多すぎる・往復辺は事実として review_reasons に残す案、部品層と thesis の支持構造から段の境界を引く案。#308 のラベル規律（label は stage 名）は変えない。"
  landed_in:
    - "docs/pipeline/theory-graph.md"
related: [IK-0753, IK-0727]
view_of: []
history: []
---

## 課題

症状: 主グラフの「消去」段に論文の式 52〜91 本、「基盤」段に 41〜56 本が入り段の境界が無い。段の間に往復辺
（消去 → 基盤 → 消去、0001⇄0002 など）があって読む順が決まらない。中心命題の到達点に当たる段が無い。
原因（仮説）: stage 集約が成員の和集合と、成員どうしの辺を持ち上げたものしか保持しない。

出所: IK-0753 の出所（uxsim/runs/radar_loop/BRAIN_NOTES_M1.md・Q1・T1・W2・X2・Z1）。

## 発見の観点

IK-0753 の材料の是正後に残る症状を、集約の規則に突き合わせて切り分けた（data_inspection）。

## 解決の観点

未解決。

## 一般化

固定語彙の段への集約は、成員の和集合と辺の持ち上げしか残さない。辞書の型 information-dropped-as-unrepresentable の近傍。
