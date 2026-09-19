---
id: IK-0330
title: resume した run では被覆報告を計算せず、stage の dict が丸ごと置換されるため前回の報告が消える
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
feature_context:
  realizing: 途中から再開した解析でも、各段階の取りこぼしを run の記録から読めるようにする
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [resume]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    統制軸: 再開経路が通常経路と異なる完了報告を書く（resume の設計）。接続軸: 完了報告の
    保存が stage 単位の丸ごと置換なので、再開時の報告に無いキー（coverage）が前回の値ごと
    失われる。構造軸: 置換か併合かは表現の選択だが、他のキーでは置換が正しいので none 寄り
    の medium。処理軸: 各関数は仕様どおり。
generalization:
  level: general
  general_form: 再開経路が省略した報告項目が、保存時の丸ごと置換によって前回の値ごと消える
pattern: context-lost-across-execution-boundary
discovery:
  perspective: [data_inspection, trace_walk]
  note: >-
    最も打ち切られた論文だけ coverage キーが無いことに気づき、run の更新日時と
    upsert の SQL（jsonb の top-level 結合）を辿って resume 経路に行き着いた。
resolution:
  perspective: [carry_through, explicit_contract]
  note: >-
    再開時も前回 run の artifact から導ける事実だけを被覆として再計算し、
    details.source="artifact" を刻んで通常実行と区別する（今回の母集合を捏造しない）。
  landed_in:
    - backend/core/document_pipeline/orchestrator.py
    - backend/tests/test_pipeline_coverage_report.py
  verification:
    methods: [guardrail]
    unverified:
      - 是正後の再解析による実データでの効果の実測（LLM の live 呼び出しを行わない方針のため次回の解析待ち）
related: [IK-0329]
view_of: []
history: []
---

## 課題

**症状**: 2026-09-17 に persist 段階だけ再実行した 3 本の run で `stage_outputs.equation_semantics.coverage`
などが無く、式 348→64 の切断が記録から読めない。

**原因**: `_attach_coverage` が `if not resumed_from_artifact:` の中だけにあり、
`upsert_analysis_run` は stage の dict を丸ごと置換する。

## 発見の観点

実データの欠落（`data_inspection`）から SQL と orchestrator の経路を追った（`trace_walk`）。

## 解決の観点

前回の事実を後段まで運ぶ（`carry_through`）。出所を明示する（`explicit_contract`）。

## 一般化

再開・再実行の経路は通常経路と同じ報告項目を書くか、書かないなら保存が併合であることを
確かめる。どちらでもないと記録が静かに減る。
