---
id: IK-0449
title: "前提の説明が番号付きの出典ゼロで具体的な値を述べ、出所が「教材に基づく」だった（確かめた結果、コース内トピックの教材を渡した経路①で、表示は正しかった）"
status: rejected
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 前提の説明の出所が、実際に渡した資料と一致する
  layers: [rag_chat]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: low
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    第 10 周の実プロンプト（req-00007 / req-00009）では前提「GRとMGでの観測量への影響」がコースのトピック題名に一致し、その教材が [コース内トピック『…』の教材] の区画で渡っていた。数値（w0
    / wa）はその教材由来で、LLM だけの経路③ではない。_resolve_prerequisite_context は course_material
    をコース内トピックの教材を渡したとき（経路①）か、コース教材のチャンクを採用したときだけに付け、経路③は model_generated +
    閉世界の事実文。_generate_learning_advice_response は現在トピックの教材を渡さない（前提名だけ）。欠陥は無い。処理軸 wording（low）: 出所の帯と番号付きの出典チップが別の情報源を指すことが、読み手に伝わっていない。
generalization:
  level: general
  general_form: 出所の分類が正しいのに、番号付きの出典が無いことから誤りに見える
pattern: wording-mismatch
discovery:
  perspective: [data_inspection, trace_walk]
  note: 第 10 周（c-astro-verify-wave456）の transcript の往復ごとの sources と mailbox の実プロンプト（req-*.json）を並べた。
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: >-
    規則をテストで固定した: 題名が一致しても教材が空なら model_generated、教材を渡したときだけ course_material（番号付き出典はゼロでもよい）。コードは変えていない。
  landed_in:
    - backend/tests/test_ik0444_0451_chat_turn_fixes.py
related: [IK-0430]
view_of: []
history: []
---

## 課題

出典ゼロで「教材に基づく」と表示される。

## 発見の観点

実プロンプトで渡った区画を確かめた。

## 解決の観点

規則を明示してテストで固定する（欠陥なし）。

## 一般化

正しい分類が番号付きの出典の不在から誤りに見える型。
