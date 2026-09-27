---
id: IK-0369
title: "受講登録 API の応答は id と title だけを埋めた LearningCourseOut を返すため、is_published / is_enrollable / visibility が既定値（false / private）のまま届き、公開コースに登録した直後の画面が「非公開・受講不可」と読める"
status: open
recorded_at: 2026-09-27
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が公開コースに受講登録する
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [none]
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
    接続軸: `routes/learning.py::enroll_course` は `LearningCourseOut(id=..., title=...)` だけを返し、DTO の他の列は
    既定値で埋まる（contract。同じ DTO を返す一覧 API は実値を埋める）。確認: 実ペルソナ run の学生段で、一覧が
    受講可能・公開と示した直後の enroll 応答が is_published:false / is_enrollable:false / visibility:private。
generalization:
  level: repo_pattern
  general_form: 共有 DTO を部分的に埋めて返し、埋めなかった列の既定値が事実として読まれる
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction]
  note: 実ペルソナの学生段で一覧 → 受講登録の応答を並べて読んだ。
resolution:
  perspective: [pending]
  note: enroll の応答を一覧と同じ投影で埋めるか、応答を id / title だけの専用 DTO にする（既定値を事実に見せない）。
  landed_in: []
related: [IK-0361, IK-0367]
view_of: []
history: []
---

## 課題

受講登録の応答が既定値のフラグを返し、公開コースが非公開・受講不可に見える。

## 発見の観点

一覧と受講登録の応答の突き合わせ（実ペルソナ）。

## 解決の観点

未解決。応答の投影を一覧と揃える。

## 一般化

共有 DTO の部分埋め（IK-0361 / 0367 と同じ族）。
