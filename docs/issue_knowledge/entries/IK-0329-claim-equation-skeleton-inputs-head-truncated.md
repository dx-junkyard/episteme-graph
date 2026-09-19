---
id: IK-0329
title: 主張採否・式の意味解析・骨格の入力を文書順の先頭 N 件で打ち切り、被覆を報告しない
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
  - docs/pipeline/overview.md §4
feature_context:
  realizing: 論文全体から主張・式・骨格を抽出し、結論や限界の節まで構造化する
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [meaning]
    governance: [budget, completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    統制軸: 上限（予算）の掛け方が先頭切り捨てで、切った事実を完了報告に含めない
    （completion）。構造軸: 打ち切りと層化サンプリングの実装が役割判定だけに置かれ、
    他の 3 ステージが独自の先頭切りを持っていた（正本の一本化が無い）。接続軸: 式の
    被覆報告が「式ブロック」だけを母集合とし、inline 候補の切り捨てを「打ち切りなし」と
    報告していた（母集合の意味のずれ）。処理軸: 各処理は仕様どおりで単一処理の不良ではない。
generalization:
  level: repo_pattern
  general_form: 同型の入力上限が複数の段階に別実装で残り、一箇所を直しても他の段階が先頭切り捨てで結論部を落とし続ける
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection, inventory]
  note: >-
    10 本中 8 本で式が「ちょうど 64 件」、6 本で主張採否の入力が「ちょうど 96 件」という
    実データの偏りから上限定数に遡り、全ステージの入力 builder を棚卸しして残りの上限を
    列挙した。
resolution:
  perspective: [canonical_source, order_and_budget]
  note: >-
    並び順・層化サンプリング・上限解決を 1 モジュールに集約し（役割判定は委譲）、
    主張採否・式・骨格の既定を上限なし + env 明示に変えた。inline 数式だけは既定 32 の弁を
    残す（表示式は無制限）。4 ステージに共通形式の被覆報告を付け、式の母集合を候補単位に揃えた。
  landed_in:
    - src/episteme_graph/agents/stratified_sampling.py
    - src/episteme_graph/agents/claim_qualification/input_builder.py
    - src/episteme_graph/agents/equation_semantics/input_builder.py
    - src/episteme_graph/agents/paper_skeleton/input_builder.py
    - backend/core/document_pipeline/orchestrator.py
  verification:
    methods: [guardrail]
    unverified:
      - 是正後の再解析による実データでの効果の実測（LLM の live 呼び出しを行わない方針のため次回の解析待ち）
related: [IK-0101]
view_of: [IK-0101]
history: []
---

## 課題

**症状**: 74 頁の論文で 803 ブロック中先頭 151 だけが主張層に入り §3〜§5 と付録が claim 化
されない。21 頁の論文では Conclusions 全 10 ブロックが落ち、結論の数値主張が claim に無い。
式は 8/10 本でちょうど 64 件。どの打ち切りも stage_outputs に現れない。

**原因**: 役割判定の 64 打ち切り（IK-0101）を直したとき、同型の `_MAX_SPANS=96` /
`_MAX_EQUATIONS=64` / `_MAX_SECTIONS=12` が別ファイルに残った。上限の実装が段階ごとに
独立していたため、正本の是正が横に効かなかった。

## 発見の観点

実データの「ちょうど上限値」という偏り（`data_inspection`）から定数へ遡り、全 builder の
棚卸し（`inventory`）で残りを列挙した。

## 解決の観点

上限・並び・層化の正本を 1 つにし（`canonical_source`）、既定を上限なし・上限は env で明示・
打ち切りは必ず報告、という予算の統制に揃えた（`order_and_budget`）。

## 一般化

IK-0101 と同じ型の別視点。「一箇所を直した」是正は、同型実装が他にあるかを棚卸しで確認
しない限り、律速が隣の段階へ移るだけになる。
