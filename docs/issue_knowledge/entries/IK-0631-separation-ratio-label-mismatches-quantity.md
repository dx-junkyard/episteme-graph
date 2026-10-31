---
id: IK-0631
title: "比較の指標 separation_ratio の表示名「差と模擬観測のばらつきの比」が、中身（理論の出力の大きさ ÷ ばらつき）と一致していなかった"
status: resolved
recorded_at: 2026-10-04
resolved_at: 2026-10-04
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "学習者と教員が比較の指標を表示名で読み、結果の意味を判断する"
  layers: [simulation_bridge_sb, frontend_learning_ui, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
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
    確認: 比較器の separation_ratio は |理論の出力| をばらつきで割った値だが、学習画面・教員画面・ハーネスの表示名は「差と模擬観測のばらつきの比」だった。処理軸 wording = 計算は正しく呼称だけが食い違う。接続軸 meaning = 同じ指標が画面で別の量として読まれる。構造軸 none（表示名の出所は少数で、正本が 2 つある構造の問題ではない）。統制軸 none。
generalization:
  level: general
  general_form: "指標の計算は正しいが、表示名が別の量を指す言葉になっていて利用者が意味を取り違える"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, doc_code_diff]
  note: "第 22 周の学生場面 (d') で、ペルソナが分離の比を差の比として読んだ。表示名と比較器の計算式を突き合わせた。"
resolution:
  perspective: [single_point_fix]
  note: "表示名を「理論の出力と模擬観測のばらつきの比」に改め、学習画面・教員画面・ハーネスの投影で同じ語にした。"
  landed_in:
    - "frontend/public/js/simulation-lab.js"
    - "frontend/public/js/admin-simulation-bridge.js"
    - "uxsim/runner/simlab_exec.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の再演で GONE を確認していない"
      - "マニュアルとCopilot KBの記述が同じ語か"
related: [IK-0612]
view_of: []
history: []
---

## 課題

症状: 分離の比が「差とばらつきの比」として読まれる。原因: 表示名が計算の中身（理論の出力の大きさ÷ばらつき）と違う量を指していた。

## 発見の観点

ペルソナの通し受講（reproduction）で読み違いを観測し、表示名と計算式を突き合わせた（doc_code_diff）。

## 解決の観点

表示名だけを直す単点修正。指標の出所はサーバの段階ラベルと 3 箇所の表示名表で、文言だけで終わるため構造の型にはしなかった。

## 一般化

指標の呼称は計算式の変更・命名時に別の量の言葉になりやすい。型は wording-mismatch。確かめ方は、計算式と表示名を並べ、表示面（学習・教員・マニュアル）で同じ語が出ることを見ること。
