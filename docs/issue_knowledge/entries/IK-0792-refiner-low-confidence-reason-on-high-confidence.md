---
id: IK-0792
title: "component_assembly の子振り分けの材料で、reason が全件「low-confidence …」なのに同梱の confidence が 0.75〜0.85 で、低信頼の判定の基準が材料から読めない"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "親部品に結びついた主張・式を子部品へ振り分けるとき、振り分け候補の理由と確からしさを添えて LLM に判断させる"
  layers: [pipeline_a]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: low
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認済み（2026-10-17・頭脳 W1 seq 3286 で再発）: reason は _equation_is_high_confidence / _claim_is_high_confidence /
    _derivation_is_high_confidence が偽のとき一律「low-confidence …」の定型文だったが、偽になる条件は confidence だけでなく
    review_flags（missing_label 等）・semantic_status・review_status（teacher_review_required）・再構成の印を含み、confidence 0.85 でも
    偽になる。以下は起票時の仮説:
    仮説: 振り分け候補の reason を定型文「low-confidence …」で埋める条件が、同梱する confidence の値と別の判定（決定論の突合の
    経路・閾値）に基づいており、confidence 0.75〜0.85 の候補にも付く。処理軸 wording = reason の文言が実際の判定と食い違う。
    接続軸 meaning = 前段の「低信頼」と数値の confidence が後段の LLM に矛盾した意味で渡る（low: どちらが正しいかは未確認）。
    構造軸 none（low）/ 統制軸 none。確定には refiner の振り分け候補の reason を組む条件と confidence の算出を読む。
generalization:
  level: general
  general_form: "理由の定型文を付ける条件と同梱する確からしさの値が別の判定に基づくため、両者が矛盾したまま後段に渡る"
pattern: wording-mismatch
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 T1 が component_assembly の子振り分け（seq 2935・comp_005 の 12 件）の材料を読み、reason と confidence の食い違いに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md）。同じ材料で suggested_responsibility が空の項目、assumption 系の子が無く主張が unassigned に落ちること、plain_text と latex が同一文字列で \\ensuremath が残ることも記されている。"
resolution:
  perspective: [single_point_fix]
  note: "reason を同じ判定の中で実際に落ちた検査の列挙から組む（_rejudge_reason + _equation/_claim/_derivation_rejudge_checks。例: 'equation semantics needs re-judgement: review flags: missing_label'）。判定の閾値・条件そのものは変えない。"
  landed_in:
    - "src/episteme_graph/agents/component_assembly/component_refiner.py"
    - "src/tests/agents/component_assembly/test_refiner_radar_w1_regressions.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail]
    unverified:
      - "振り分けの LLM が新しい理由文で判断を変えるか（実際の振り分け結果の比較）"
related: [IK-0728]
view_of: []
history: []
---

## 課題

症状: 子振り分けの artifacts の reason が全件「low-confidence …」だが、同梱の confidence は 0.75〜0.85 で、何が低信頼なのかが
材料から読めない。
原因（仮説）: 定型の reason と confidence が別々の判定に基づいている。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2935）。

追加の出所（2610.08751・頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3286 — 振り分け理由が「low-confidence equation semantics」だが対象式の confidence は 0.85）。

追加の出所（頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3284 — 振り分け理由が「low-confidence equation semantics」なのに当該式の confidence は 0.85〜0.9）。

## 発見の観点

振り分け候補の reason と confidence を突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

理由文と数値が別判定だと、後段は矛盾した信号を受け取る。辞書の型 wording-mismatch（軽）。
