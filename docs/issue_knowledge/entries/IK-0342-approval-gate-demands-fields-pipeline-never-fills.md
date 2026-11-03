---
id: IK-0342
title: 部品の承認ゲートが要求する出典の列（source_chunks・source_refs）をパイプラインの部品が持たず、一件も承認できない
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
  - docs/features/graph_dialogue_review_design.md
feature_context:
  realizing: 教員がグラフレビューで部品を承認し、承認済みだけを下流（C層・R層・コース）に流す
  layers: [graph_review, theory_artifacts, endorsement_c]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
    governance: [assignment]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 承認ゲート（API の項目スキーマ `source_refs` / `evidence_claims`）と A層の
    出力（`claim_ids` / `equation_ids`・`source_chunks` 空）の契約が両立していない。統制軸:
    「確定は人間」の弁が構造的に閉じたまま（承認 0 件）で、下流の承認前提の層が空回り
    （担当の割り当てが実効性を持たない）。構造・処理: 要素なし。
generalization:
  level: general
  general_form: 人間の確定を求めるゲートが、生成側が埋めない項目を必須にしているため、確定の弁が一度も開かない
pattern: contract-changed-one-side
discovery:
  perspective: [data_inspection, invariant_audit]
  note: >-
    live 部品 211 件に承認可能性判定を適用して 0 件であることを確認し、原則 1（確定は
    人間）が実データで機能していないことから項目スキーマの語彙差に遡った。
resolution:
  perspective: [carry_through, explicit_contract]
  note: >-
    読み時に claim_ids / equation_ids を出典として受理し、source_chunks は根拠 claim の
    chunk_id から導出する（列は書き換えない）。出典必須の弁は維持。
  landed_in:
    - backend/api/routes/theory_components.py
    - backend/api/schemas.py
    - backend/tests/test_pipeline_component_approval.py
  verification:
    methods: [guardrail]
    unverified:
      - docker で組み上げた実機での E2E
related: [IK-0301]
view_of: []
history: []
---

## 課題

**症状**: 0/211 部品が承認可能。承認の下流（C層・R層・delivered_unreviewed）が空回り。

**原因**: ゲートの項目スキーマとパイプライン出力の語彙が違う。

## 発見の観点

実データ（`data_inspection`）と不変条項の照合（`invariant_audit`）。

## 解決の観点

材料を読み時に運ぶ（`carry_through`）。弁の条件は明示して維持（`explicit_contract`）。

## 一般化

確定の弁は「生成側が実際に埋める項目」で定義する。埋まらない項目を必須にすると弁は閉じたまま計器にも出ない。
