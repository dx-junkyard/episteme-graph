---
id: IK-0731
title: "doubt:scope_candidates の対象に序論の問題提起文・背景文が選ばれ、適用条件を持ちえない文で LLM コールを使う"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "認識的地位台帳の主張に、検証された条件・領域・精度（検証スコープ）の候補を LLM で提案する"
  layers: [doubt_d, pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [budget]
  axis_confidence:
    processing: medium
    structure: low
    connection: low
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 検証スコープ候補の対象選別が、主張の claim_type・修辞上の役割（背景・問題提起・先行研究の記述）を見ず、
    台帳行のある主張を一律に対象にしている。処理軸 logic = 対象の判定に文種が入っていない。統制軸 budget = 候補を
    持ちえない文に日次上限のある LLM コールを使い、4 件中 3 件が候補なし。接続軸 information = 上流の修辞役割・
    claim_type が対象選別に届いていない可能性（低確信）。構造軸 none（低確信）。確定には scope_candidates worker の
    対象抽出 SQL と、ledger_builder が台帳行を作る主張の範囲を読む。
generalization:
  level: general
  general_form: "高価な処理の対象を、処理が意味を持つ種別かどうかで絞らずに全件から選ぶため、結果が空になる対象で予算を使う"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 M0 が doubt:scope_candidates（seq 2133・2136・2139・2142）に応答し、問題提起文・背景文・先行研究の慣行の記述が対象に入り候補が空になることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_M0.md）。唯一候補が出た seq 2136 も出典は claim 本文 1 文のみで周辺文脈が無かった。"
resolution:
  perspective: [pending]
  note: "未解決。対象を結果・方法の主張（claim_type と rhetorical_role）に絞り、背景・問題提起は LLM を呼ばずに対象外として記録する案。"
  landed_in:
    - "docs/features/doubt_layer_issues.md"
related: [IK-0730]
view_of: []
history: []
---

## 課題

症状: 「トライスペクトルを含むフルシェイプ解析は未実施」「n 点関数の階層」「連星計算の多くは遠方平坦を仮定」のような
序論の問題提起文・背景文が検証スコープ候補の対象に選ばれ、4 件中 3 件が候補なし。
原因（仮説）: 対象選別が claim_type・修辞役割を見ていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_M0.md（seq 2133・2136・2139・2142）。

## 発見の観点

LLM に渡された対象の文種と、返った候補の有無を突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

意味を持たない対象に高価な処理を回す。辞書の型 extent-decided-by-proxy-signal（対象を「台帳行がある」で決める）。
