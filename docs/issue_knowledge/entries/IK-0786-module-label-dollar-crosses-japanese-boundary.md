---
id: IK-0786
title: "照合の対象外モジュールの名指しで、数式の区切り $ が和文の項目境界をまたぎ、命令名の残骸（textbfx）が残る"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §22.4"
feature_context:
  realizing: "理論モジュールの照合結果の事実文で、照合の対象外にしたモジュールをラベルで名指しする"
  layers: [graph_review, paper_radar]
classification:
  axes:
    processing: [input_handling]
    structure: [responsibility]
    connection: [none]
    governance: [none]
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
    確認（DB の SELECT と関数の再実行）: 保存行の visual_label は $ なしのプレーンで、related がそれを「、」で連結した事実文を
    教員向け表示投影の delimit_bare_tex が語単位（空白区切り）で読み、和文を含む「R_δ(λ)、定義:」を 1 語として $…$ で囲んでいた
    （処理軸 input_handling: 和文混じりの入力を語に割れない）。加えてプレーン化の関数がビルダー内にあり、保存時の旧規則の
    visual_label（\textbf の剥がし漏れ）を related がそのまま使っていた（構造軸 responsibility: 表示ラベルを作る責務が保存時と
    読み時に分かれていた）。接続・統制は none（伝達・手続の欠落ではない）。
generalization:
  level: general
  general_form: "表示用に整形済みの文字列を、後段が再び数式の区切り付けの対象にし、語の区切りの前提（空白区切り）が和文で崩れて区切りが項目をまたぐ"
pattern: rule-enforced-per-surface
discovery:
  perspective: [data_inspection, trace_walk]
  note: "深い段の審判が deep-07.json の related の facts で「定義: $R_δ(λ)、定義$: η」を見つけ、保存行（DB）の visual_label は正常で、教員向け投影の delimit_bare_tex が崩していることを関数の再実行で確かめた。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "プレーン化の正本 plain_math を core/theory_modules/schema.py に置き（builder は import）、\\textbf 系・\\langle / \\cdots / \\parallel を足した。related は保存行の visual_label を信用せず $ 付きの label から読み時に作り直す。delimit_bare_tex は和文の文字を語の区切りとして扱い、区切りが和文をまたがない。"
  landed_in:
    - backend/core/theory_modules/schema.py
    - backend/core/theory_modules/related.py
    - backend/core/latex_display.py
    - "docs/features/paper_radar_design.md §22.4"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "事実文・保存行・グラフレビューの DTO のラベルを schema.plain_math 1 つに通す。module_match_note はラベルを持たないので対象外。"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "画面（グラフレビューの外枠詳細）での KaTeX 描画"
      - "保存行の visual_label 自体の再生成（再解析までは旧規則の値が残る。読み時に作り直すので表示には出ない）"
related: [IK-0778]
view_of: []
history: []
---

## 課題

症状: 照合の対象外モジュールを名指しする事実文に「定義: $R_δ(λ)、定義$: η」「O_L,parallel(textbfx)」が出た。
原因: ①related が連結したプレーンのラベルを、教員向け投影の裸の TeX 区切り付けが空白区切りの語として読み、和文ごと `$…$` で囲んだ
②保存時のプレーン化（ビルダー内）が `\textbf{x}` を剥がせず、related はその保存値を使っていた。

出所: uxsim/runs/radar_loop/judge-deep-07.md ③-1・④-2。

## 発見の観点

DTO の事実文と、DB の保存行・投影関数の出力を突き合わせた（data_inspection / trace_walk）。

## 解決の観点

ラベルのプレーン化を 1 つの正本に寄せ、読み時に作り直す。区切り付けの語の切り方を和文に対応させる（canonical_source / guardrail_fix）。

## 一般化

整形済みの表示文字列を後段の整形に再び通すときは、後段の語の切り方の前提（空白区切り・ASCII）が入力で成り立つかを確かめる。
