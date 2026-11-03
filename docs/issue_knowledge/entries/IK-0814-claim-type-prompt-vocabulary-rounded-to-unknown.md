---
id: IK-0814
title: "claim_qualification のプロンプトが許す主張の型（relation / diagnostic_claim / correction / uncertainty）を claim_object_builder の語彙が持たず、unknown に丸めて後段へ渡す"
status: resolved
recorded_at: 2026-10-17
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/knowledge_objects_design.md"
feature_context:
  realizing: "主張の採否と型を LLM が判定し、決定論の組立段で最終の主張オブジェクトの型に写して後段（部品組立・保存）へ渡す"
  layers: [pipeline_a, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認済み: 主張の型の語彙が 3 箇所にあり（claim_qualification の CORE_CLAIM_TYPE_FALLBACKS = プロンプトと検証、
    claim_object_builder の CLAIM_TYPE_ONTOLOGY = 組立の正規化、backend core/schema.py の CLAIM_TYPES = 保存の正本）、
    組立段の語彙だけが relation / correction / uncertainty / diagnostic_claim を欠いて _normalize_claim_type が unknown を
    返していた。構造軸 aggregation = 同じ語彙表の正本が複数ある。接続軸 contract = 前段が出してよい型と後段が受ける型が
    両立しない。処理軸・統制軸は無関係（順序・予算・文言の問題ではない）。
generalization:
  level: general
  general_form: "前段が出してよい語彙と後段が受ける語彙を別々に持つため、前段が正しく出した値が後段で既定値に丸められて失われる"
pattern: duplicate-canonical-sources
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 W1 が component_assembly（2610.08751・seq 3283）の available_claims を読み、自分が claim_qualification で返した relation / diagnostic_claim が unknown になっていることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md）。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "組立段の語彙に 4 型を足し（いずれも正本 CLAIM_TYPES に在るので migration は不要）、プロンプトが許す型 ⊆ 組立段の語彙 ⊆ 正本、をテストで固定した。claim_qualification のプロンプト側は別エージェントの編集中のため触らず、受け手の語彙を前段に合わせた。"
  landed_in:
    - "src/episteme_graph/agents/claim_object_builder/schema.py"
    - "backend/tests/test_claim_type_vocab_alignment.py"
    - "src/tests/agents/component_assembly/test_refiner_radar_w1_regressions.py"
  verification:
    methods: [guardrail]
    unverified:
      - "2610.08751 の再解析で available_claims の型の内訳に unknown が残らないことの実測"
      - "保存済みの theory_claims に既に丸められた unknown の行（再解析まで残る）"
related: [IK-0784]
view_of: []
history: []
---

## 課題

症状: claim_qualification が relation / diagnostic_claim と返した主張が、component_assembly の available_claims では claim_type=unknown になっている。
原因: claim_object_builder の CLAIM_TYPE_ONTOLOGY がプロンプトの許す型の一部を持たず、_normalize_claim_type が語彙外として unknown に丸めていた。

## 発見の観点

前段で返した型と後段の入力の型を突き合わせた（data_inspection）。

## 解決の観点

受け手の語彙を前段（プロンプト）に合わせ、3 つの語彙の包含関係をテストで固定した（canonical_source + guardrail_fix）。プロンプト側を正本から
生成する案は、claim_qualification が別作業の編集中のため見送った。

## 一般化

前段と後段が別々の語彙表を持つとどこでも起こる。辞書の型 duplicate-canonical-sources。IK-0784（接続語の語彙のプロンプトと検証の食い違い）と同族。
