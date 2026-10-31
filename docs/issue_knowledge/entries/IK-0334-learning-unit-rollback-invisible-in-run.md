---
id: IK-0334
title: 学ぶ単位の永続化が一意制約で全件ロールバックしても run は完了扱いで、失敗は artifact の中にしか残らない
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
feature_context:
  realizing: 論文の「教える単位」を一級の行として永続化し、コース側の候補に出す
  layers: [learning_units, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [completion]
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
    接続軸: 永続化の結果要約（inserted/failed）が artifact にだけ書かれ、run の
    stage_outputs と計器（reference_health）に運ばれない。統制軸: 部分失敗でも run の
    完了判定が変わらない。処理軸: 一意制約違反そのものは IK-0323 で解決済みで本件の
    原因ではない。構造軸: 要素なし。
generalization:
  level: general
  general_form: 副次的な永続化の失敗が主経路の完了判定に反映されず、成功した run と区別できない
pattern: failure-reported-as-success
discovery:
  perspective: [data_inspection, trace_walk]
  note: >-
    12 本中 1 本だけ学ぶ単位が 0 件であることから artifact を開き、UniqueViolation の記録と
    stage_outputs に knowledge_objects キーが無いことを突き合わせた。
resolution:
  perspective: [carry_through, explicit_contract]
  note: >-
    4 系統の永続化の結果要約を run の stage_outputs.knowledge_objects に必ず書き、失敗は
    別セッションで {failed, error} を残す。reference_health が「学ぶ単位の記録がありません」
    を事実文で出す。データの再生は再解析（オーナー操作）。
  landed_in:
    - backend/core/document_pipeline/persistence.py
    - backend/core/reference_health.py
  verification:
    methods: [guardrail]
    unverified:
      - 是正後の再解析による実データでの効果の実測（LLM の live 呼び出しを行わない方針のため次回の解析待ち）
      - docker で組み上げた実機での E2E
related: [IK-0323]
view_of: []
history: []
---

## 課題

**症状**: 2609.15827v1 の学ぶ単位が 0 件、run は completed、参照健全性は ok。

**原因**: 永続化の要約が artifact 止まり。原因の制約違反は IK-0323 で解消済みだが、
その事実が run 側に無く、再生の必要性が誰にも見えなかった。

## 発見の観点

実データの偏り（`data_inspection`）→ artifact の失敗記録（`trace_walk`）。

## 解決の観点

要約を後段へ運び（`carry_through`）、計器の事実文として宣言する（`explicit_contract`）。

## 一般化

主経路が成功しても副次の永続化が落ちる設計では、完了判定は「何が入ったか」を要約で持つ必要がある。
