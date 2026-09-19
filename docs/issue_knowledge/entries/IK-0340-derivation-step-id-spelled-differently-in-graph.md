---
id: IK-0340
title: 導出 step の ID が DB では合成キー、グラフでは裸の step ID で綴られ、辺の導出参照が一件も解決しない
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
  - docs/features/knowledge_objects_design.md §12.2
feature_context:
  realizing: グラフの辺・ノードから導出 step の行へ辿り、参照健全性を検査する
  layers: [knowledge_objects, pipeline_a, knowledge_transfer]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: step ID はチェーン内でしか一意でなく、DB 側は合成キーで一意化したが
    グラフ側は裸 ID のまま（同じ対象の綴りが 2 つ）。接続軸: Phase 1 が DB 側だけ契約を
    変え、グラフを書く側と読む側の契約が両立していない。処理・統制: 要素なし。
generalization:
  level: repo_pattern
  general_form: 識別子の一意化を一方の書き手だけに適用したため、同じ対象を指す綴りが経路ごとに分かれ参照が解決しない
pattern: id-unique-only-within-inner-scope
discovery:
  perspective: [data_inspection]
  note: >-
    辺の evidence_derivation_ids の DB 解決率が 0% であることと、DB の agent ID が
    `{derivation_id}:{step_id}` 形であることを突き合わせた。
resolution:
  perspective: [explicit_contract, representation_change]
  note: >-
    グラフ側に合成 ID を裸 ID と併記し（node_id は不変）、読み側は両綴りを解決する。
    A層と backend の規則の一致はテストで固定（A層に knowledge 層の語は書かない）。
  landed_in:
    - src/episteme_graph/agents/component_graph/schema.py
    - backend/core/knowledge_objects/references.py
    - backend/core/reference_health.py
related: [IK-0107, IK-0323, IK-0303]
view_of: [IK-0107]
history: []
---

## 課題

**症状**: 辺の `evidence_derivation_ids` が DB に 0% 解決。

**原因**: 一意化（合成キー）を永続化側だけに適用し、グラフの書き手は裸 ID のまま。

## 発見の観点

実データ（`data_inspection`）。

## 解決の観点

綴りの契約を明示し（`explicit_contract`）、グラフ側の表現に合成 ID を加える
（`representation_change`）。

## 一般化

IK-0107 の別視点。一意化は「その ID を書く全経路」に同時に適用する。
