---
id: IK-0349
title: 主張採否の上限を撤廃したのに、そのステージが何回 LLM を呼んだかが記録も注記もされていない
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §7.1
feature_context:
  realizing: 主張採否の母集合を打ち切らずに処理する
  layers: [pipeline_a, usage_metering_u]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [budget]
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
    統制軸: 呼び出し回数が span 数に比例するよう変えた（予算の性質を変えた）のに、その事実を run の記録（summary_stats / stage_outputs）にも文書にも残さなかった。他の軸: 要素なし。確認手段: claim_qualification のsummary_stats に llm_calls が無く、ProviderJSONLLMClient に計数が無いこと。
generalization:
  level: repo_pattern
  general_form: 処理量の上限を外すと外部呼び出しの量が入力に比例するようになるが、その量を記録・注記しないため運用で予算が読めない
pattern: external-budget-exceeded
discovery:
  perspective: [adversarial_review]
  note: >-
    上限撤廃（E-2 / J-3）の変更を、コストの観点から読み直した。
resolution:
  perspective: [explicit_contract, doc_correction]
  note: >-
    共通の JSON クライアントに calls 計器を置き、agent が run ごとの差分を summary_stats.llm_calls に書き、orchestrator が stage payload に写す。文書に「回数は span 数に比例する」と注記する。
  landed_in:
    - src/episteme_graph/agents/llm_json_client.py
    - src/episteme_graph/agents/claim_qualification/agent.py
    - docs/pipeline/overview.md §4
related: [IK-0329]
view_of: []
history: []
---

## 課題

**症状**: 上限撤廃後の run で、主張採否が何回 LLM を呼んだかが run の記録から分からない。

**原因**: 回数を数える計器が agent 側に無く、文書にも予算の性質が変わったことを書いていなかった。

## 発見の観点

敵対的レビュー（`adversarial_review`）。

## 解決の観点

回数を明示の値として残し（`explicit_contract`）、文書を直す（`doc_correction`）。

## 一般化

上限を外す変更は「量が何に比例するか」を記録と文書に残す。予算の性質の変更は無言で行わない。
