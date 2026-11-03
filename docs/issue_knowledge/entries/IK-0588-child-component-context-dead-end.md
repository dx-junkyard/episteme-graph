---
id: IK-0588
title: "分割された子部品の文脈に来た元の親部品が上位に出ず、1 hop 先が行き止まりで戻れない"
status: resolved
recorded_at: 2026-10-01
resolved_at: 2026-10-01
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.9
feature_context:
  realizing: "ペルソナ通し受講 第 17 周（4 つの根の是正後の再演・c-astro-structure-30）の場面"
  layers: [learner_experience_b, deliberation_w, learning_units]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 第 17 周の再演（ハーネス是正後）で、部品「TOV residual constraint」の下位へ 1 hop 移ると移動先の上位・下位がともに「なし」で、来た元が上位に出なかった。子部品は Phase 2 の `parent_agent_component_id` を持つが、学習者向け部品文脈の上位レーンに親を出す配線が無い疑い（接続軸 information）。IK-0570（上位レーンが id=None）と同じく上へ辿る参照の欠落。component_context の上位レーンが親部品を読むかを確かめれば確定する。
generalization:
  level: repo_pattern
  general_form: "下へ辿る参照だけを作り、辿った先から元へ戻る参照を作らない"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: "第 17 周 s-trace-one-hop 再演の頭脳メモ（行き止まり・戻る導線なし）から。"
resolution:
  perspective: [carry_through, guardrail_fix]
  note: '分割された部品の文脈に、分割の親（行として残らない LLM 原案はラベルのみ）・同じ論文の子・被依存の部品を上位レーンに出し、1 hop 先から戻れるようにした。'
  landed_in:
    - backend/core/component_context.py
    - frontend/public/js/app.js
    - backend/tests/test_ik0588_component_context_hops.py
    - e27ee17
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "ブラウザでの画面確認"
      - "分割の親が行として残らない論文では、親は移動できないラベルのまま（設計どおり・利用者の受け止めは未確認）"
related: [IK-0570, IK-0554]
view_of: []
history:
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'open'
    reason: '2026-10-01 第 18 波で是正中（別担当がコードを是正している）。'
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '第 18 波（e27ee17）で是正し、第 18 周の再演（st-01 s-trace-one-hop）で 4 hop すべてに戻り先があり行き止まりが無いこと、子部品の上位に元の部品と親が出ることを見た。（persona_enactment_testing_design.md §18.11）'
---

## 課題

1 hop の移動はできるようになったが、移った先から上にも下にも辿れず、旅が 1 hop で詰まる。

## 発見の観点

第 17 周 s-trace-one-hop 再演の頭脳メモ（行き止まり・戻る導線なし）から。

## 解決の観点

解決（e27ee17）。子部品の文脈の上位に分割の親（行が無ければラベルのみ）・同じ論文の子・被依存を出し、1 hop 先から戻れるようにした。
起票時の原因は仮説（classification.basis）のままで、是正後の再演で行き止まりが消えたことを確かめた。

## 一般化

下へ辿る参照だけを作り、辿った先から元へ戻る参照を作らない。
