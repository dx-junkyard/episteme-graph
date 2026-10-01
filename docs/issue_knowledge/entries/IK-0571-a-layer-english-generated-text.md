---
id: IK-0571
title: "A層由来の英語の生成文（⚓ 本文・summary・teaching_takeaway・図キャプション）が日本語の学習画面にそのまま出る"
status: open
recorded_at: 2026-09-30
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [pipeline_a, learner_experience_b]
classification:
  axes:
    processing: [unknown]
    structure: [representation]
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
    仮説: 構造課題。表示側で隠すか A層で日本語を生成するか、翻訳の置き場が未定。 原因の性質で座標を置いた（症状の場所ではなく）。 未解決のため座標は暫定。
generalization:
  level: general
  general_form: "生成物の言語を表示先の言語と照合する段が無い"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [pending]
  note: >-
    一部着手（2026-10-01）。run options `language` を paper_skeleton / thesis_reconstruction /
    narrative_annotator / component_assembly の生成文に運んだ（未指定なら prompt 不変・LLM 回数不変）。
    component の label / summary と equation_semantics の summary は、決定論の後段が英語キーワードで
    構造を導くため対象外（generation_language_design.md §2 GL4）。既存教材は再解析が要り、砂場での
    確認は未実施のため open のまま。
  landed_in:
    - src/episteme_graph/agents/generation_language.py
    - backend/core/document_pipeline/orchestrator.py
    - docs/features/generation_language_design.md
related: [IK-0546, IK-0540]
view_of: []
history:
  - date: '2026-10-01'
    field: resolution.landed_in
    from: "[]"
    to: >-
      src/episteme_graph/agents/generation_language.py / {paper_skeleton,thesis_reconstruction,
      narrative_annotator,component_assembly}/{prompt,agent}.py / orchestrator._run_language・
      _language_run_kwargs
    reason: >-
      生成言語を 4 agent に運んだ。component の summary / label・式の summary は構造を導く後段が読むため
      未対応で、砂場の再解析で確かめるまで open のまま
---

## 課題

A層由来の英語の生成文（⚓ 本文・summary・teaching_takeaway・図キャプション）が日本語の学習画面にそのまま出る。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

未解決。構造課題。表示側で隠すか A層で日本語を生成するか、翻訳の置き場が未定。

2026-10-01: A層で生成言語を指定する側を選び、run options `language` を 4 agent（paper_skeleton / thesis_reconstruction / narrative_annotator / component_assembly）の生成文へ運んだ。component の summary / label と式の summary は後段の英語キーワード照合が構造を決めるため対象外で、残る（`generation_language_design.md` §5）。

## 一般化

原因は仮説（未確定）。
生成物の言語を表示先の言語と照合する段が無い。
