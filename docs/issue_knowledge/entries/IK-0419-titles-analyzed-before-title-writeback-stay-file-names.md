---
id: IK-0419
title: "題名の書き戻し（IK-0366）より前に解析された教材は、文書構造が題名を取り出していても documents.title がファイル名・arXiv 番号のまま残り、再解析するまで学習者の位置づけ・論文の海・進捗に番号で出続ける"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 教材の題名を、解析済みの文書構造から学習者の各画面へ届ける（A層 → 教材メタデータ）
  layers: [pipeline_a, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
    governance: [resume]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    統制軸: IK-0366 の書き戻しは解析 run の upsert のときだけ走り、既に解析済みの教材を1回直す経路（起動時の冪等バックフィル）が無かった（resume）。接続軸: 採用 run の document_structure artifact には題名があるのに、それより前の版の documents.title が残った（version。medium）。処理軸: 書き戻し自体の判定（仮の題名か・改行を含むか）は正しい（none）。確認: 第 8 周の学生が全画面で「論文が番号でしか出ない」と記録。
generalization:
  level: repo_pattern
  general_form: 新しく足した書き戻しが以後の実行にしか効かず、既存の行を直す一度きりの経路が無い
pattern: stale-derivative-served
discovery:
  perspective: [reproduction]
  note: 学生ペルソナがどの画面でも論文を番号で受け取った。
resolution:
  perspective: [order_and_budget]
  note: >-
    persistence.backfill_document_titles_from_structure を足し、lifespan で専用キー（DOCUMENT_TITLE_BACKFILL_LOCK_KEY）の advisory xact lock 配下・fail-open で起動時に走らせる。対象は題名が仮の値（document_title_is_placeholder）の教材だけで、採用 run（resolve_artifact_runs と同じ adopted の選び方）の document_structure 段だけを読み、extracted_document_title が採れれば書き戻す（人が付けた題名は UPDATE の一致条件で上書きしない・冪等）。題名が採れない教材は仮のまま（推測しない）。教材一覧・学習者の各画面は documents.title を読むので同じ効果が届く。
  landed_in:
    - backend/core/document_pipeline/persistence.py
    - backend/api/main.py
    - backend/tests/test_document_title_backfill.py
  verification:
    methods: [guardrail]
    unverified:
      - 実 Postgres での実行（docker 未達）
      - 砂場での再演（再起動後に題名が変わること）
      - GROBID が題名を取り出せなかった教材は番号のまま残る
related: [IK-0366]
view_of: []
history: []
---

## 課題

書き戻し導入前に解析した教材の題名が番号のまま。

## 発見の観点

実ペルソナの全画面での観測（第 8 周）。

## 解決の観点

既存の行を一度だけ直す起動時の冪等経路を足す。

## 一般化

以後にしか効かない修正は、既存の行を直す経路とセットにする。
