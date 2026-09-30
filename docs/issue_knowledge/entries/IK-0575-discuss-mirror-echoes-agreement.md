---
id: IK-0575
title: "discuss の鏡が学習者の同意の前置き文を復唱し、問いの核を外す"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
  - docs/features/dialogue_response_shape_design.md
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [discuss]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: low
    structure: low
    connection: low
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 2 回連続で観察。鏡の規則の矛盾（IK-0548）と同根の可能性。 原因の性質で座標を置いた（症状の場所ではなく）。 未解決のため座標は暫定。
generalization:
  level: general
  general_form: "引用規則が逐語であることだけを求め、どの部分を映すかを定めない"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [canonical_source, responsibility_move]
  note: "鏡の引用を学習者の発話の核心語へ狭める mirror_core を新設（前置き・同意・挨拶の引用は落とし、前置きで始まる引用は剥がす。核心語が残らなければ鏡を出さない）。保存文と応答の両方に掛ける。プロンプトのルール 2 にも同じ規則を 1 文足した。"
  landed_in:
    - backend/core/dialogue_shape.py
    - backend/api/routes/learning.py
    - backend/tests/test_dialogue_shape_core.py
    - backend/tests/test_dialogue_shape_route.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（ペルソナ通し受講の次の周）
      - 実 LLM 出力での問いの切り分けの取りこぼし（箇条書き末尾の問いなど）
related: [IK-0548]
view_of: []
history: []
---

## 課題

discuss の鏡が学習者の同意の前置き文を復唱し、問いの核を外す。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

core/dialogue_shape.py::mirror_core が鏡の引用を核心語へ狭める（応答の骨格 RS4）。

## 一般化

原因は仮説（未確定）。
引用規則が逐語であることだけを求め、どの部分を映すかを定めない。
