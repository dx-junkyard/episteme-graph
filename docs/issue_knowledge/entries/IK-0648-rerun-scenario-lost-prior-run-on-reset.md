---
id: IK-0648
title: "前周の実行を前提にする再演場面が、周の前の学習者状態のリセットで前周の実行を消され、経路が止まって観察できなかった"
status: resolved
recorded_at: 2026-10-04
resolved_at: 2026-10-04
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "ペルソナ通し受講の運転で、前周の成果を前提にする場面を再演する"
  layers: [cycle_verification]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [ordering]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 周の前の reset は対象のペルソナの SB層の行を消すため、第 21 周 (e')(g) の再演が前提にする共有 run が無くなり、run_id 不在で止まった。製品の欠陥ではない。統制軸 ordering = リセットと再演の順で前提が壊れる。接続軸 condition = 場面の前提（前周の run が要る）がリセットの対象選びに伝わらない。処理軸 none。構造軸 none。
generalization:
  level: instance
  general_form: "準備手順が後の場面の前提を消し、前提を持つ場面が観察できないまま終わる"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction]
  note: "第 22 周で第 21 周 (e')(g) を再演しようとして経路が run_id 不在で止まった。"
resolution:
  perspective: [carry_through, order_and_budget]
  note: "前周の run が要る場面は reset の除外（--exclude-persona）に入れるか、その場面の前提を同じ周の場面側へ付け替える運転にした。"
  landed_in:
    - "uxsim/sandbox/reset_learner_state.py"
  verification:
    methods: [reproduction_rerun]
    unverified:
      - "第 21 周 (e')(g) の再演そのものは第 22 周では回せていない（除外の運転は場面 (l) でだけ確かめた）"
related: [IK-0626, IK-0634]
view_of: []
history: []
---

## 課題

症状: 再演場面が run_id 不在で止まる。原因: 周の前のリセットが前提の run を消した。

## 発見の観点

運転中の再演（reproduction）で止まった経路から見えた。

## 解決の観点

リセットの除外か前提の付け替えで前提を残す。場面 (l) では除外で前周の run を残して経路が通った。

## 一般化

準備の一括リセットは、後の場面の前提を黙って消す。近い型は condition-not-propagated（前提の条件が準備手順へ伝わらない）。確かめ方は、前提を持つ場面を準備の直後に流し、前提が残っていることを見ること。
