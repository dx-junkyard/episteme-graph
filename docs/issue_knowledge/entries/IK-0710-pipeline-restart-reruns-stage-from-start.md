---
id: IK-0710
title: "自律周回中の API サーバ再起動のたびに走行中の解析が止まり、resume が役割判定のステージを最初からやり直して同じブロックの LLM 呼び出しが繰り返される"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/architecture/improvement_cycle.md"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "検証の周回中に製品を是正・再起動しながら、取り込んだ論文の解析パイプラインを最後まで通す"
  layers: [cycle_verification, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [none]
    governance: [resume, ordering]
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
    確認（頭脳の応答記録で同じ document・同じブロックの役割判定要求が 4 回届いたこと）: 是正のたびに api-server を再起動し、
    走行中の解析が止まった。resume の再開単位はステージで、役割判定のステージ内の進捗（ブロックごとの結果）は保存されないため、
    最初のブロックからやり直した。統制軸 resume = 停止と再開の単位がステージ単位で、途中の結果を捨てる。統制軸 ordering = 解析が
    走行中に再起動する運転の順序（IK-0601 と同じ）。構造軸 decomposition = 再開の単位（ステージ）が LLM 呼び出しの単位（ブロック）
    より大きい。処理軸 none = 各ブロックの判定は正しい。接続軸 none = 段階間で値は落ちていない（同じ入力を作り直している）。
generalization:
  level: general
  general_form: "長い処理の再開単位が内部の呼び出し単位より大きく、中断のたびに完了済みの呼び出しをやり直して費用が重なる"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection, trace_walk]
  note: "LLM を肩代わりした頭脳の応答記録で、同じ document・同じブロックの要求が 2 回目・3 回目・4 回目と再来することに気付き、再起動と resume の経路を辿った（出所: uxsim/runs/radar_loop/BRAIN_NOTES_C1.md・BRAIN_NOTES_C3.md）。"
resolution:
  perspective: [pending]
  note: "未解決。提案: ステージ内のチェックポイント（ブロック単位の部分 artifact を保存し、resume は未処理のブロックから再開する）。運転側は IK-0601 と同じく、解析の走行中は再起動しない順序を守る。"
  landed_in:
    - "docs/architecture/improvement_cycle.md"
related: [IK-0601, IK-0699]
view_of: []
history: []
---

## 課題

症状: 論文レーダーの自律周回で、同じ document・同じブロックの役割判定の LLM 要求が 4 回届いた。
原因: 是正のたびに api-server を再起動し、走行中の解析が止まる。resume はステージ単位で再開し、役割判定のステージ内の
ブロックごとの結果を保存していないため、最初からやり直した。役割判定は本文ブロック 1 件 = 1 回の呼び出し（IK-0699）なので、
やり直しの費用が大きい。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_C1.md（763・811・901 番などの再来の記録）・BRAIN_NOTES_C3.md（897 番「4 回目の再解析」）。

## 発見の観点

頭脳の応答記録を読み（data_inspection）、同じブロックの再来から再起動と resume の経路を辿った（trace_walk）。

## 解決の観点

未解決。ステージ内のチェックポイント（ブロック単位の部分 artifact）を置けば、中断しても完了済みのブロックを再利用できる。
運転の順序（走行中は再起動しない）は IK-0601 で決めた手順を周回でも守る。辞書の reexecution 系の型（人の判断の上書き）は当たらないため、
再開単位の未定義として unit-of-work-undefined に当てた。

## 一般化

ステージ単位の冪等性だけを持つパイプラインは、内部で多数の外部呼び出しをするステージで中断の費用が重なる。
