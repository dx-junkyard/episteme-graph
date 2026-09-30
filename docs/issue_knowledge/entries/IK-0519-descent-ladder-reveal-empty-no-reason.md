---
id: IK-0519
title: "構造の降下路で、開示段が空のときに理由がなく、どの要素について降りているか（対象名）も DTO に無かった"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/features/structure_descent_design.md
feature_context:
  realizing: "足場ダイヤルで要素の構造を一段ずつ降りる"
  layers: [structure_descent]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    target_label と、reveal 空の reason 2 種（式リンクなし / 手順なし）を足した。 原因の性質で座標を置いた（症状の場所ではなく）。
generalization:
  level: general
  general_form: "出せなかった段を空のまま返し、空である理由と対象の名前を落とす"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "学生ペルソナが空の段で行き止まりになった。"
resolution:
  perspective: [explicit_contract]
  note: "target_label と、reveal 空の reason 2 種（式リンクなし / 手順なし）を足した。"
  landed_in:
    - backend/core/descent/engine.py
    - backend/core/descent/resolve.py
    - frontend/public/js/app.js
    - backend/tests/test_descent_core.py
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified: []
related: []
view_of: []
history:
  - date: '2026-09-30'
    field: resolution.verification
    from: guardrail のみ・砂場再演は未確認
    to: guardrail + 砂場再演
    reason: >-
      第 15 周（persona_enactment_testing_design.md §18.7）の再演で症状の消失を頭脳メモが GONE と記録した。
---

## 課題

降下路の空段に理由が無かった。

## 発見の観点

学生ペルソナが空の段で行き止まりになった。

## 解決の観点

target_label と、reveal 空の reason 2 種（式リンクなし / 手順なし）を足した。

## 一般化

出せなかった段を空のまま返し、空である理由と対象の名前を落とす。
