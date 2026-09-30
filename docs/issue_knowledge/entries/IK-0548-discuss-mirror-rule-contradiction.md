---
id: IK-0548
title: "discuss の鏡（〔鏡〕）の規則でプロンプトのルール 2 と 6 が矛盾する"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-09-30
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
  - docs/features/dialogue_response_shape_design.md
feature_context:
  realizing: "ペルソナ通し受講 第 14 周（uxsim/runs/c-astro-structure-30）（c-astro-structure-30）の場面"
  layers: [discuss, seminar_brief]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: low
    structure: low
    connection: low
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    矛盾した規則が LLM に渡る。 未解決のため原因の座標は暫定。
generalization:
  level: repo_pattern
  general_form: "discuss の鏡（〔鏡〕）の規則でプロンプトのルール 2 と 6 が矛盾する"
pattern: contract-changed-one-side
discovery:
  perspective: [data_inspection]
  note: "第 14 周の審判 C・頭脳メモ・製品 prompt から。"
resolution:
  perspective: [explicit_contract, responsibility_move]
  note: "ルール 2（鏡の確認）を優先し、ルール 6 に「末尾の問いは1つだけ」を足した。モデルが両方を出しても、鏡のあるターンの末尾の問い返しはサーバ（core/dialogue_shape.py::assemble）が落とす（RS3）。"
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
related: []
view_of: []
history: []
---

## 課題

discuss の鏡（〔鏡〕）の規則でプロンプトのルール 2 と 6 が矛盾する。矛盾した規則が LLM に渡る。

## 発見の観点

第 14 周の運転記録から。

## 解決の観点

ルール 2 を優先し、末尾の問いの数はサーバが 1 つに制限する（応答の骨格 RS3）。

## 一般化

未確定。
