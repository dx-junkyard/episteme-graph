---
id: IK-0588
title: "分割された子部品の文脈に来た元の親部品が上位に出ず、1 hop 先が行き止まりで戻れない"
status: open
recorded_at: 2026-10-01
resolved_at: null
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
  perspective: [pending]
  note: "未解決。第 17 周の記録（§18.9）で起票した。"
  landed_in: []
related: [IK-0570, IK-0554]
view_of: []
history:
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'open'
    reason: '2026-10-01 第 18 波で是正中（別担当がコードを是正している）。'
---

## 課題

1 hop の移動はできるようになったが、移った先から上にも下にも辿れず、旅が 1 hop で詰まる。

## 発見の観点

第 17 周 s-trace-one-hop 再演の頭脳メモ（行き止まり・戻る導線なし）から。

## 解決の観点

未解決。原因は仮説で、classification.basis に確かめる点を書いた。

## 一般化

下へ辿る参照だけを作り、辿った先から元へ戻る参照を作らない。
