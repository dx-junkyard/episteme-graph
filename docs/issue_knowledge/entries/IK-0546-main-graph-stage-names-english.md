---
id: IK-0546
title: "主グラフの stage 名（Theory basis 等）が英語のまま教員・学習者に見える"
status: resolved
recorded_at: 2026-09-30
resolved_at: 2026-10-01
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.6
feature_context:
  realizing: "ペルソナ通し受講 第 14 周（uxsim/runs/c-astro-structure-30）（c-astro-structure-30）の場面"
  layers: [graph_review, pipeline_a]
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
    #308 のラベル規律と日本語表示の関係が未決。 未解決のため原因の座標は暫定。
generalization:
  level: repo_pattern
  general_form: "主グラフの stage 名（Theory basis 等）が英語のまま教員・学習者に見える"
pattern: wording-mismatch
discovery:
  perspective: [data_inspection]
  note: "第 14 周の審判 C・頭脳メモ・製品 prompt から。"
resolution:
  perspective: [vocabulary_table]
  note: >-
    graph_json の main label（#308 の英語の stage 名）と validator は変えず、表示する側が
    `element_vocab.theory_stage_display_label()` を通す。グラフレビューの canvas は IK-0566
    （display_label）で解消済みだったので、英語の段名を出していた残り 3 経路（学習者の旅 [1] /
    グラフ全体対話の grounding / SA層のグラフレビュー解決器）を同じ表に通した。
  landed_in:
    - backend/core/element_vocab.py
    - backend/core/personal_graph/journey.py
    - backend/core/deliberation/graph_dialogue.py
    - backend/core/assistant_context/resolvers/graph_review.py
    - docs/features/generation_language_design.md §4
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: >-
        採った部品は ①1 箇所の正本（element_vocab の訳語表と表示関数）と ④面ごとの読み替えを委譲だけに
        すること。③全経路を走査する横断検査は作っていない（main label を表示する新しい面が表を
        迂回しても赤にならない）。
  verification:
    methods: [guardrail]
    unverified:
      - "砂場再演（旅・グラフ全体対話・SA層の画面で英語の段名が消えたことを実画面で確かめていない）"
      - "main label を表示する経路の網羅（横断検査が無い）"
related: [IK-0566, IK-0571]
view_of: []
history:
  - date: '2026-10-01'
    field: status
    from: open
    to: resolved
    reason: 残り 3 経路を element_vocab の訳語表に通した（ユニット / ガードレールで確認）
---

## 課題

主グラフの stage 名（Theory basis 等）が英語のまま教員・学習者に見える。#308 のラベル規律と日本語表示の関係が未決。

## 発見の観点

第 14 周の運転記録から。

## 解決の観点

#308 の規律（graph_json の main label は英語の stage 名）は変えず、表示する側が `element_vocab.theory_stage_display_label()` を通す。IK-0566 で canvas は解消済みだったので、旅 [1]・グラフ全体対話の grounding・SA層のグラフレビュー解決器を同じ表に通した。

## 一般化

未確定。
