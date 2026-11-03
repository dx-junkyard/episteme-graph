---
id: IK-0634
title: "uxsim ハーネスの検証シミュレーションの投影に観測系の主張の一覧と実行履歴が出ず、一致しない主張名を黙って空にして共有で候補が立たなかった"
status: resolved
recorded_at: 2026-10-04
resolved_at: 2026-10-04
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "ペルソナ通し受講のハーネスが学習画面と同じ情報を投影し、行為の指定（主張名）を実体へ解決する"
  layers: [cycle_verification, tests_guardrails]
classification:
  axes:
    processing: [input_handling]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: uxsim/runner/simlab_exec.py の open / select の投影に観測系の主張の一覧が無く、_claim_ids_for は学生が自分で作った主張名が一覧に無いとき黙って空を返した。投影は一定字数で切り詰められ、実行履歴が末尾で落ちた。構造軸 representation = 一致しなかった指定と切り捨てた履歴を残す受け皿（runner_notes）に書いていなかった。接続軸 information = 画面にある情報がペルソナに届かない。処理軸 input_handling = 一致しない入力を黙って空にする。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "テストハーネスが画面の情報の一部を投影せず、解決できない指定を黙って空にして、製品の挙動と区別できない偽の所見を生む"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 22 周の学生場面 (e'') で共有しても候補が立たず、(j)(l) で実行履歴が見えなかった。ハーネスの投影と解決関数を読み、製品ではなくハーネスの欠落と分かった。"
resolution:
  perspective: [representation_change, carry_through]
  note: "投影に主張の一覧と選んだ主張を出し、一致しない指定は runner_notes と 1 行で残し、実行履歴を一覧の上へ移して切り詰めで落ちないようにした。"
  landed_in:
    - "uxsim/runner/simlab_exec.py"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified: []
related: [IK-0628]
view_of: []
history:
  - date: 2026-10-04
    field: resolution.verification
    from: 記録時の範囲
    to: 第 22 周の再演で更新
    reason: "第 22 周 (e''-only) で GONE"
---

## 課題

症状: ペルソナが観測系の主張を選べず、共有しても台帳の候補が立たない。前の実行も見えない。原因: ハーネスの投影に主張の一覧が無く、一致しない主張名を黙って空にし、投影の切り詰めで実行履歴が落ちていた。

## 発見の観点

ペルソナの通し受講（reproduction）で候補の欠落を観測し、ハーネスの投影と画面の欄を突き合わせた（data_inspection）。

## 解決の観点

画面と同じ欄を同じ順で投影し、解決できない指定は受け皿に残す。黙って空にする代わりに失敗を明示するのは、偽の所見を製品の課題と取り違えないため。

第 22 周: (e''-only) で主張 2 つが結び、候補が 2 件立った（GONE）。

## 一般化

検証用の投影が画面より情報を落とすと、ハーネスの欠落が製品の課題に見える。型は information-dropped-as-unrepresentable。確かめ方は、解決できない指定と長い投影を意図的に作り、runner_notes と投影に残ることを見ること。
