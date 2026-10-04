---
id: IK-0640
title: "コードを生成し直したとき、前の採用版との差分・どちらが採用中か・前の版で走らせた実行との対応が画面に出ない"
status: open
recorded_at: 2026-10-04
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "学習者が理論プログラムを生成し直し、前の版と比べて採用を決め、過去の実行と対応づける"
  layers: [simulation_bridge_sb, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
    governance: [none]
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
    確認: 再生成は旧行を superseded にして superseded_by_id で新行を指し、実行は program_id で版を持つ（migration 088）。データには版の関係があるが、学習画面は差分・採用中の版・実行と版の対応を出していない。接続軸 version = 版の関係が画面へ渡っていない。処理軸 none。構造軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "版の関係を記録しているのに画面へ出さず、利用者が版の違いと過去の結果の対応を読めない"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: "第 22 周の学生場面 (d') で、生成し直した後にどの版の結果を見ているかが分からなくなった。"
resolution:
  perspective: [pending]
  note: "未解決（軽微）。版の関係（superseded_by_id・program_id）は既にあるので、実行一覧に版を、生成後に前の版との差分を出せば解ける。"
related: [IK-0634]
view_of: []
history: []
---

## 課題

症状: 生成し直した後、どちらが採用中か、過去の実行がどの版のものかが画面で分からない。原因: 版の関係は保存されているが、学習画面の DTO と表示に配線されていない。

## 発見の観点

ペルソナの通し受講（reproduction）で、版の取り違えに迷った場面から見えた。

## 解決の観点

未解決。新しい保存は要らず、既存の版の関係を表示へ運ぶだけで足りる見込み。

## 一般化

版を保存しても表示に出さないと、結果と版の対応が利用者の記憶頼みになる。型は available-but-unwired。確かめ方は、生成し直した後の実行一覧で各実行の版と採用中の版が読めることを見ること。
