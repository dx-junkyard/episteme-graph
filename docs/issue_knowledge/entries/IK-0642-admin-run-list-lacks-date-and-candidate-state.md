---
id: IK-0642
title: "教員側の実行一覧には日時と台帳候補の有無が出ず、学習者側の一覧（日時あり）と同じ実行の見え方が面ごとに違う"
status: open
recorded_at: 2026-10-04
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "教員が学習者の共有した実行の一覧から、いつのどの実行が候補になったかを見分ける"
  layers: [frontend_admin_ui, simulation_bridge_sb]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 学習者側の実行一覧は日時と ledger_decision を出すが、教員側の一覧は日時・候補の有無を出していない。実行の表示項目が面ごとに組まれている。構造軸 responsibility = 実行の要約を組む責務が面ごとに分かれている。接続軸 information = 情報はあるが教員の面へ届かない。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "同じ対象の一覧を面ごとに組み立て、一方の面で直した表示項目がもう一方の面に入らない"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction]
  note: "第 22 周の教員場面 (g') で、両教員が実行の時期と候補の有無を見分けられなかった（学生場面 (l) では日時が出ていた）。"
resolution:
  perspective: [pending]
  note: "未解決。実行の要約（日時・状態・候補の判断）をサーバの 1 関数で組み、両方の面が同じ欄を出せば解ける。"
related: [IK-0634]
view_of: []
history: []
---

## 課題

症状: 教員の一覧で実行の時期と候補になったかが分からない。原因: 実行の表示項目を面ごとに組み、学習者側だけに日時が入った。

## 発見の観点

ペルソナの通し受講（reproduction）で教員が実行を見分けられなかった場面から、学習者側の一覧と比べた。

## 解決の観点

未解決。要約を 1 箇所で組む方向。

## 一般化

面ごとに組む一覧は、片方だけ改善される。型は rule-enforced-per-surface。確かめ方は、同じ実行を学習者・教員の両方の面で開き、同じ欄が出ることを並べて見ること。
