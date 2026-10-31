---
id: IK-0367
title: 教材の一覧 API は原本の有無（has_pdf）を MinIO で確かめて返すのに、詳細 API は組み立てておらず常に false になるため、詳細だけを見る呼び出し側は原本が無いと誤認する
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-27
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 教材の詳細から原本（PDF）を開く・原本の有無で操作を出し分ける
  layers: [frontend_admin_ui]
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
    IK-0361 と同じ形。同じ `MaterialOut` を返す一覧と詳細で、詳細側だけ `has_pdf` を組み立てていない（contract）。
    処理軸は単一処理の欠落（logic）。確認: 実ペルソナ run 20260927T044913Z のステップ 4（一覧: has_pdf true）と
    5（詳細: false）。
generalization:
  level: repo_pattern
  general_form: 同じ DTO を返す一覧と詳細で、片方だけが後から足された列を埋めていない
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction]
  note: 実ペルソナの教員段で一覧と詳細の応答を並べて読んだ（IK-0361 の是正直後に同じ型が残っていた）。
resolution:
  perspective: [single_point_fix]
  note: >-
    詳細側で原本の有無を MinIO に 1 件だけ問い合わせて埋めた（一覧側と同じ判定・同じ縮退）。一覧と詳細の組み立てを
    1 つに寄せる整理は未実施（IK-0361 と同じ残課題）。
  landed_in:
    - backend/api/routes/admin.py
  verification:
    methods: [reproduction_rerun, docker_e2e]
    unverified:
      - MinIO 不達時の詳細側の縮退（一覧と同じ False になること）
      - 一覧と詳細の組み立て関数の統合
related: [IK-0361]
view_of: []
history: []
---

## 課題

教材の詳細 API で `has_pdf` が常に false。一覧は MinIO を見て true を返す。

## 発見の観点

一覧と詳細の応答の突き合わせ（IK-0361 と同じ経路の再演）。

## 解決の観点

詳細側でも原本の有無を確かめる（`_material_has_source_object`）。砂場を再ビルドし、一覧と詳細で一致することを API で確かめた。

## 一般化

IK-0361 と同じ型。同じ DTO の 2 経路は 1 つの組み立て関数を通す。
