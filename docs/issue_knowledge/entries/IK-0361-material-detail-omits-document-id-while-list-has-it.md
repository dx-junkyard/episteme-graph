---
id: IK-0361
title: 教材の一覧 API は document_id を返すのに、同じ MaterialOut を返す教材の詳細 API は document_id を組み立てておらず null になるため、詳細から成果物 API（document_id が要る）へ進めない
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-27
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 教員が教材を 1 本選び、解析結果（グラフ・論文層・図）へ進む
  layers: [frontend_admin_ui, knowledge_objects]
classification:
  axes:
    processing: [logic]
    structure: [none]
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
    接続軸: `GET /api/admin/materials` と `GET /api/admin/materials/{id}` は同じ `MaterialOut` を返す契約だが、
    後者の組み立て（`backend/api/routes/admin.py::get_material`）は `document_id` を渡していない（contract。
    同じ DTO の 2 経路で埋まる列が違う）。処理軸: 単一処理の欠落（logic）。確認: 砂場の 4 教材で一覧は
    document_id あり・詳細は null（run 20260927T033148Z のステップ 4・5）。構造軸・統制軸は関係しない（none）。
generalization:
  level: repo_pattern
  general_form: 同じ DTO を返す一覧と詳細で、片方だけが後から足された列を埋めていない
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    ペルソナ通し受講（教員段）が一覧 → 詳細の順に叩いた応答を突き合わせて見えた。
resolution:
  perspective: [single_point_fix, canonical_source]
  note: >-
    詳細側の SELECT に d.id を足し、一覧と同じ document_id を組み立てた（single_point_fix）。一覧と詳細を 1 つの組み立て関数に寄せる整理（canonical_source）は従で未実施。
  landed_in:
    - backend/api/routes/admin.py
  verification:
    methods: [reproduction_rerun, docker_e2e]
    unverified:
      - 一覧と詳細の組み立て関数の統合（別々のまま）
      - document 行が無い material（解析前）での None の扱い
related: []
view_of: []
history: []
---

## 課題

教材の詳細 API の応答で `document_id` が null になる。一覧 API は同じ DTO で document_id を返す。詳細を起点に
成果物 API（component-graph・paper-layer・figures は document_id が要る）へ進む経路が、詳細だけを見た呼び出し側で
切れる。原因は詳細側の `MaterialOut(...)` の組み立てに `document_id` が無いこと（列は SELECT していない）。

## 発見の観点

ペルソナ通し受講の教員段（`t-build-course-from-corpus`）で `admin.materials.list` → `admin.materials.get` の応答を
並べて読んだ（reproduction / trace_walk）。

## 解決の観点

詳細側の SELECT に `d.id::text AS document_id` を足し、`MaterialOut(document_id=...)` に渡した。砂場を再ビルドし、
一覧と詳細の document_id が一致することを API で確かめた（run 20260927T033929Z の前）。一覧と詳細を 1 つの組み立て
関数に寄せる整理は行っていない。

## 一般化

一覧に後から列を足し、詳細を直し忘れる型。DTO を共有する 2 経路は 1 つの組み立て関数を通す。
