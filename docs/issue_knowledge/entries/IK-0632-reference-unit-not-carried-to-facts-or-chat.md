---
id: IK-0632
title: "比べる論文の記載値の単位・出所が入力にはあったのに結果の事実文と学習チャットの文脈へ渡らず、AI が単位の換算ずれを疑わせる誤った説明をした"
status: resolved
recorded_at: 2026-10-04
resolved_at: 2026-10-04
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "学習者が論文の記載値と理論プログラムの出力を比べ、その結果を学習チャットで尋ねる"
  layers: [simulation_bridge_sb, screen_adapter_sa]
classification:
  axes:
    processing: [none]
    structure: [none]
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
    確認: 記載値の入力は記号・単位・出所を持つが、比較器の facts と SA層 4-e の解決器の事実文には記載値の存在も単位も載っていなかった。接続軸 information = 前段にある情報が後段（結果表示・AI の文脈）へ配線されていない。処理軸 none（比較の計算は正しい）。構造軸 none。統制軸 none。
generalization:
  level: general
  general_form: "入力に付いていた前提（単位・出所）が結果と対話の文脈へ運ばれず、後段の説明者が前提を推測して誤った原因を示す"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: "第 22 周の学生場面 (i) で、AI が記載値を別の単位のまま比べた可能性を挙げた。記載値の入力から facts と画面文脈の事実文まで辿り、単位がどこにも渡っていないと分かった。"
resolution:
  perspective: [carry_through, single_point_fix]
  note: "schema.reference_input_fact（値は書かず記号・単位・出所）を正本にし、比較器の facts と SA層の解決器の事実文の両方が同じ関数で 1 行を出す。"
  landed_in:
    - "backend/core/simulation_bridge/schema.py"
    - "backend/core/simulation_bridge/comparator.py"
    - "backend/core/assistant_context/resolvers/learning.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の再演で GONE を確認していない"
      - "AI が単位の行を読んで換算の誤りを疑わなくなるか"
related: [IK-0619]
view_of: []
history: []
---

## 課題

症状: 記載値と比べた結果について、AI が単位の換算ずれを原因として示した。原因: 記載値の単位と出所は入力にあったが、結果の事実文にも学習チャットへ渡す画面文脈にも運ばれていなかった。

## 発見の観点

ペルソナの通し受講（reproduction）で誤った説明を観測し、入力から事実文・画面文脈までの経路を辿った（trace_walk）。

## 解決の観点

単位・出所の 1 行を正本関数にして両方の経路へ配線した。値そのものは入力欄にあるので書かない（事実文に数字を載せない規律）。

## 一般化

比較の前提は結果と一緒に運ばれないと、後段の説明者が補って誤る。型は available-but-unwired。確かめ方は、記載値を入れた実行で結果表示と AI の文脈の両方に単位の行が現れることを実際の操作で見ること。
