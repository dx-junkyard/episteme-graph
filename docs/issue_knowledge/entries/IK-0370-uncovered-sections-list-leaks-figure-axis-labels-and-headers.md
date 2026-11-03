---
id: IK-0370
title: "コース内容の生成が「学ぶ単位が立っていない章」として列挙する uncovered_sections に、図の軸目盛（'40'', '100', 'S8'）・表のセル・論文ヘッダ・参考文献行がそのまま入り、学習者のコース画面と教員の草案に章名として表示される"
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース登録後にコース内容を生成し、覆えなかった章を利用者に伝える
  layers: [course_builder, pipeline_a, frontend_learning_ui]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [meaning]
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
    接続軸: 文書構造の `sections` には見出しでないブロック（図の軸目盛・表セル・ヘッダ・参考文献）が「節」として
    混ざっており、コース内容生成はそれを節名として信じて uncovered_sections に載せる（meaning。前段の「節」の意味が
    後段では「章の見出し」に読み替わる）。処理軸: 節名の妥当性（数字だけ・短すぎる・改行を含む）を検査していない
    （input_handling）。確認: 砂場コース 900588b4 の `course_content_status.uncovered_sections`（'40'' '100' 'S8'
    'Draft version June 2, 2026 Typeset using LATEX…' 'REFERENCES' 等）と、学生ペルソナのコース画面の観測。
generalization:
  level: repo_pattern
  general_form: 前段が「節」と呼ぶものの純度を後段が検査せず、見出しでない断片を見出しとして利用者に見せる
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [reproduction, data_inspection]
  note: 実ペルソナの学生段でコース画面の「学ぶ単位が立っていない章」を読み、砂場 DB の course_content_status と突き合わせた。
resolution:
  perspective: [fail_closed, guardrail_fix]
  note: >-
    節名の妥当性検査（改行・数字と単位だけ・短すぎ・定型のヘッダ／参考文献／表の列名）を後段に置き、落とした文字列は run 内部の uncovered_sections_dropped に残す。根本の節判定は別課題。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0370_uncovered_section_title_filter.py
  verification:
    methods: [reproduction_rerun, docker_e2e, guardrail]
    unverified:
      - 文書構造側の節判定（GROBID / PDF 経路の見出し誤認）
      - 学習者向け DTO に course_content_status を出すこと自体の妥当性（別途）
related: []
view_of: []
history: []
---

## 課題

覆えなかった「章」の一覧に、図の目盛や表のセル・論文ヘッダ・参考文献が章名として並ぶ。

## 発見の観点

学生ペルソナのコース画面と砂場 DB の突き合わせ。

## 解決の観点

未解決。節名の妥当性検査を後段に置き、根本（節判定）は別課題。

## 一般化

段の境で型（節＝見出し）を検査しない型。
