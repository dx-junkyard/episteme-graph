---
id: IK-0710
title: "自律周回中の API サーバ再起動のたびに走行中の解析が止まり、resume が役割判定のステージを最初からやり直して同じブロックの LLM 呼び出しが繰り返される"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-22
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
  perspective: [order_and_budget, first_class_state]
  note: "役割判定のステージ内チェックポイントを置いた。agent は窓を 1 つ終えるごとにブロックごとの結果（ブロックの指紋 = ID + 本文 + 形式の版、結果の種別、annotation）を checkpoint_callback で渡し、orchestrator が stage_outputs._stage_checkpoints.rhetorical_role に書く（進捗の stage_outputs.rhetorical_role には載せない — タスクのポーリング応答に展開されるため）。resume（start_stage なし）では前回 run の状態を agent に渡し、指紋と分野が一致する判定済みブロックは問い直さない（提供元障害で unknown になったブロックは引き継がず問い直す）。artifact は従来どおり完了時に 1 回だけ書き（KO6 不変）、チェックポイントは完了時と、引き継がない run（restart・新規）の最初に外す。注入された agent が checkpoint 引数を受けないときは渡さない。運転側の順序（解析の走行中は再起動しない — IK-0601）は不変。"
  landed_in:
    - "backend/core/document_pipeline/orchestrator.py"
    - "src/episteme_graph/agents/rhetorical_role/window.py"
    - "src/episteme_graph/agents/rhetorical_role/agent.py"
    - "backend/tests/test_rhetorical_role_stage_checkpoint.py"
    - "src/tests/agents/rhetorical_role/test_windowing.py"
    - "docs/pipeline/overview.md"
    - "docs/architecture/improvement_cycle.md"
  verification:
    methods: [guardrail]
    unverified:
      - "実 LLM・実 DB での中断と resume（api-server を走行中に再起動し、同じブロックの要求が再来しないこと）は行っていない"
      - "チェックポイントの書き込みが stage_outputs を大きくする量と、書き込み頻度の DB 負荷"
      - "役割判定以外の LLM ステージ（主張採否など）の中断費用"
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

再開の単位を LLM 呼び出しの単位（窓・ブロック）まで下げた（order_and_budget）。ブロックごとの作業状態を run の
一級の状態として持たせ、指紋で照合して再利用する（first_class_state）。artifact（1 run × 1 stage の生成ログ）とは分けた。
運転の順序（走行中は再起動しない）は IK-0601 で決めた手順を周回でも守る。辞書の reexecution 系の型（人の判断の上書き）は当たらないため、
再開単位の未定義として unit-of-work-undefined に当てた。

## 一般化

ステージ単位の冪等性だけを持つパイプラインは、内部で多数の外部呼び出しをするステージで中断の費用が重なる。
