---
id: IK-0550
title: "target.material_id の ID 体系（source_path 形と documents.id）が経路で食い違う"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-10-01
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "ペルソナ通し受講 第 14 周（uxsim/runs/c-astro-structure-30）（c-astro-structure-30）の場面"
  layers: [rag_chat, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
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
    同名キーに 2 つの ID 体系。 未解決のため原因の座標は暫定。
generalization:
  level: repo_pattern
  general_form: "target.material_id の ID 体系（source_path 形と documents.id）が経路で食い違う"
pattern: id-namespace-conflated
discovery:
  perspective: [data_inspection]
  note: "第 14 周の審判 C・頭脳メモ・製品 prompt から。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: 'document_id は常に documents.id とし、source_path 形と documents.id の両形を判別してよいのは _resolve_document と core/document_refs に限定した。その外での両形比較はガードレールが弾く。'
  landed_in:
    - backend/core/document_refs.py
    - backend/api/services.py
    - backend/api/routes/admin.py
    - backend/api/routes/reconstruction.py
    - backend/tests/test_document_refs.py
    - cb301bb
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（次の周で確かめる）"
      - ブラウザでの画面確認
related: []
view_of: []
history:
  - date: '2026-10-01'
    field: status
    from: 'open'
    to: 'resolved'
    reason: '第 18 波で document_id の規則を documents.id に一本化し、両形の判別を 2 箇所に限定するガードレールを置いた。（persona_enactment_testing_design.md §18.10）'
---

## 課題

target.material_id の ID 体系（source_path 形と documents.id）が経路で食い違う。同名キーに 2 つの ID 体系。

## 発見の観点

第 14 周の運転記録から。

## 解決の観点

両形の判別を _resolve_document と core/document_refs の 2 箇所に寄せ、他の経路は documents.id だけを扱う（cb301bb）。

## 一般化

未確定。
