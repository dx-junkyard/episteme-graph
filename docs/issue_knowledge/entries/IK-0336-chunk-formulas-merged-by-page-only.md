---
id: IK-0336
title: チャンクへの式プレビュー併合がページ一致だけで判定するため、ページ番号が信用できない文書で全式が全チャンクに入る
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
  - docs/architecture/knowledge_structure_review_2026-09-12/A_fidelity.md
feature_context:
  realizing: 学習画面の教材チャンクに、そのチャンクに属する式を KaTeX で埋め込む
  layers: [pipeline_a, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: 併合条件がページ一致のみで、より強い一致条件（block_id）があるのに使わない。
    接続軸: 上流のページ番号が誤付与（多数が page=1）という条件が後段に伝わらず、
    後段はページを信用して判定する。構造・統制: 要素なし。
generalization:
  level: general
  general_form: 信頼できない粗い属性で対応付けを行い、信頼できる細かい属性が同じレコードにあるのに優先しない
pattern: scope-widened-silently
discovery:
  perspective: [data_inspection]
  note: >-
    chunk の formulas のうち当該 chunk の block_ids 外が 87〜94% であること、ページが 1 に
    集中していることから併合関数の判定条件に遡った。
resolution:
  perspective: [single_point_fix, fail_closed]
  note: >-
    block_id ∈ chunk.block_ids を第一条件にし、ページ一致は block_ids が無く相異なるページが
    2 つ以上実在するときだけのフォールバックにする。
  landed_in:
    - backend/core/document_pipeline/persistence.py
    - backend/tests/test_equation_preview_scope.py
  verification:
    methods: [guardrail]
    unverified:
      - 是正後の再解析による実データでの効果の実測（LLM の live 呼び出しを行わない方針のため次回の解析待ち）
related: [IK-0328]
view_of: []
history: []
---

## 課題

**症状**: 20 チャンク中 17 が同じ 44 式を持つ。出典ポップアップ・レクチャーの式が誤る。

**原因**: `_merge_equation_previews_for_chunk` がページ一致だけで判定。

## 発見の観点

実データ（`data_inspection`）。

## 解決の観点

条件を強い属性に置き換え（`single_point_fix`）、判定できないときは併合しない（`fail_closed`）。

## 一般化

対応付けは最も細かい共通キーで行い、粗いキーは信頼できる条件下のフォールバックに限る。
