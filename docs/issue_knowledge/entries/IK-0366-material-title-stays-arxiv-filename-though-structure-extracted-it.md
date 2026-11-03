---
id: IK-0366
title: 文書構造の解析が論文の題名を取り出しても documents.title へ運ばれず、教材一覧・コースビルダー・学習者のコース情報では題名が arXiv 番号のファイル名（2605.26810v1）のままになるため、教員は一覧のどれがどの論文か分からない
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 教材一覧から論文を選んでコースを作る／学習者がコースの出典を見る
  layers: [pipeline_a, frontend_admin_ui, course_builder]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    接続軸: `document_structure` の成果物 `metadata.title` に題名が入る（砂場の 4 本のうち 1 本で
    「Neutron Star Equation of State via Physics Informed Neural Network」）が、`documents.title` は URL 取得時の
    ファイル名の語幹（2605.31198v1）のまま更新されない（information。前段が持つ情報が後段の表示へ運ばれない）。
    残る 3 本は metadata.title 自体が None で、GROBID / PDF 経路の題名抽出も安定していない（別の課題として
    切り分ける）。確認: 砂場 DB の documents.title と document_analysis_artifacts(stage=document_structure)
    の突き合わせ、教員ペルソナの反応（「どれがレビュー論文か分からない」）。
generalization:
  level: repo_pattern
  general_form: 前段が取り出した表示用の情報（題名）が、後段の正本列に書き戻されず、入口の仮の値が表示され続ける
pattern: available-but-unwired
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    実ペルソナの通し受講（run 20260927T044913Z）で教員ペルソナが教材一覧を読んで混乱し、砂場 DB で成果物と
    documents.title を突き合わせた。
resolution:
  perspective: [carry_through]
  note: >-
    document_structure の保存時に metadata.title を documents.title へ書き戻す（題名がファイル名語幹などの仮の値のときだけ・冪等・fail-soft）。
  landed_in:
    - backend/core/document_pipeline/persistence.py
    - backend/tests/test_ik0366_document_title_writeback.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再解析（snapshot は是正前に解析したので題名は arXiv 番号のまま）
      - metadata.title が None になる 3 本（題名抽出そのものは別課題）
related: [IK-0361]
view_of: []
history: []
---

## 課題

教材の題名が arXiv 番号のファイル名のまま表示される。文書構造の成果物には題名が入っている場合があるが、
`documents.title` へ書き戻す経路が無い。

## 発見の観点

実ペルソナの教員段（教材一覧 → 詳細）と砂場 DB の成果物の突き合わせ。

## 解決の観点

未解決。成果物の題名を正本列へ運ぶ（人が編集した題名は保持）。

## 一般化

前段の情報が後段の正本に配線されていない型（available-but-unwired）。
