---
id: IK-0333
title: 日次のコール上限を同日投入の先着数本が使い切り、残りの論文は文脈説明が 0 件のまま run が完了扱いになる
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
feature_context:
  realizing: 論文の要素に二層説明（一般 / 文脈）の候補を生成し、教員レビューへ流す
  layers: [pipeline_a, hierarchical_explanation, guidance_g]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [budget, completion]
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
    統制軸: 予算が日単位で、document 単位の最小枠が無い（budget）。上限で 0 件のときも
    run は completed で、教員に見える場所に事実が出ない（completion）。接続軸: skip の
    事実は stage_outputs には残っているので情報は落ちていない（none。ただし消費側へ
    運ばれていない点で medium）。処理・構造: 要素なし。
generalization:
  level: general
  general_form: 共有の予算が枯渇したときの「やらなかった」が完了と同じ顔で記録され、誰にも見えない
pattern: external-budget-exceeded
discovery:
  perspective: [data_inspection, inventory]
  note: >-
    10 本の stage_outputs を並べ、8 本が skipped_by_limit:true・llm_calls:0 であることを
    確認した。同日投入の順序と日次上限 20 から原因を特定した。
resolution:
  perspective: [explicit_contract, deferred_decision]
  note: >-
    予算の意味論は変えず、G層 To-Do「material.explanations_skipped」（説明が 1 件でも
    生成されれば消える）と reference_health の事実文で見えるようにした。run 単位の按分は
    オーナー判断 O-A として保留。
  landed_in:
    - backend/core/admin_assistant/next_steps.py
    - backend/core/reference_health.py
    - backend/core/coverage_facts.py
related: [IK-0308]
view_of: []
history: []
---

## 課題

**症状**: 8/11 本で要素の二層説明が 0 件。`element_explanations` は discussion_seed だけ。
run は completed、To-Do なし。

**原因**: `CTXEXPL_MAX_CALLS_PER_DAY=20` が日単位で、バッチ取り込みの先着が使い切る。
skip は stage_outputs に正直に残るが消費側に出ない。

## 発見の観点

実データの棚卸し（`data_inspection` / `inventory`）。

## 解決の観点

事実を宣言として消費側に出す（`explicit_contract`）。予算の按分は保留（`deferred_decision`）。

## 一般化

共有予算の枯渇は「失敗」ではないが「完了」でもない。第三の状態を計器に出さないと、
完了扱いのまま欠落が固定される。
