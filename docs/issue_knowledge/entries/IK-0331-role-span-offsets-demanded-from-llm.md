---
id: IK-0331
title: 役割判定の span 検証が LLM に文字オフセットの計算を要求するため、長いブロックはほぼ全件が修復失敗で unknown になる
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
feature_context:
  realizing: 本文ブロックの各 span に論理的役割を付け、主張候補を選別する
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [contract]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: 逐語一致を保証する責務（オフセットの計算）を LLM 側に置いていた。接続軸:
    validator の「span.text は block の逐語スライス」という契約に対し、LLM 出力は本文を
    返せてもオフセットを正確には返せない（両立しない契約）。統制軸: 修復失敗が fallback で
    成功扱いになり、被覆報告は「全件処理」と言う（完了の定義が緩い）。処理軸: 検証の
    判定自体は正しい。
generalization:
  level: general
  general_form: 生成側が保証できない厳密値の計算を生成側の責務にし、検証で落ちた結果を既定値で埋めて成功として数える
pattern: failure-reported-as-success
discovery:
  perspective: [data_inspection, trace_walk]
  note: >-
    span の reason が 65〜98% で「Repair failed after max attempts」であること、成功する
    ブロックが短い断片だけであることから validator の error 条件と repair の fallback に遡った。
resolution:
  perspective: [responsibility_move, explicit_contract]
  note: >-
    オフセットの計算主体をサーバに移す（LLM の返した本文を block 内で決定論的に探して
    訂正。見つからなければ従来どおり error）。検証基準は緩めない。修復失敗・unknown 比率を
    summary_stats に出す。
  landed_in:
    - src/episteme_graph/agents/rhetorical_role/repair.py
    - src/episteme_graph/agents/rhetorical_role/agent.py
    - src/tests/agents/rhetorical_role/test_offset_correction.py
related: [IK-0329]
view_of: []
history: []
---

## 課題

**症状**: 12 本すべてで span の 65〜98% が `role_labels=["unknown"]`・confidence 0。主張採否の
選別が「unknown を全部拾う」= 位置選抜に堕ちる。被覆は 455/455 と報告する。

**原因**: `_check_span_content` が逐語スライス一致を error にし、LLM に 1,800 字の正確な
オフセットを要求する。修復ループを使い切ると fallback を返し成功に数える。

## 発見の観点

実データの reason 分布（`data_inspection`）から validator と repair を辿った（`trace_walk`）。

## 解決の観点

責務の移動（`responsibility_move`）: 厳密値はサーバが計算する。契約の明示（`explicit_contract`）:
検証基準は不変、失敗は計測に出す。

## 一般化

LLM に要求してよいのは意味の判断で、逐語の位置は決定論で確定する。失敗を既定値で埋めて
完了に数えると、計器が「全件処理」と言いながら中身が空になる。
