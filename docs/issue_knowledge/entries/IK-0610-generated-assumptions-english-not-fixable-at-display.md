---
id: IK-0610
title: "式の前提（assumptions）は解析段が英語で生成し生成言語の対象外のため、表示側では日本語にできず、学習者に英語の長い列が出る"
status: deferred
recorded_at: 2026-10-03
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §19
  - docs/features/simulation_bridge_design.md §14
feature_context:
  realizing: "理論モジュールの前提を学習者に読める形で示す"
  layers: [pipeline_a, simulation_bridge_sb, frontend_learning_ui]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [information, meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 前提の文は A層の式の意味付けステージが英語で生成し、生成言語の指定（generation_language_design）の対象外のため、下流の表示では言語を変えられない。表示側でできるのは出所の明示と折りたたみだけ。接続軸 information / meaning = 上流で決まった言語が下流で取り戻せない。処理軸 input_handling = 下流は受け取った言語のまま扱うしかない。確定には、生成言語の対象ステージ一覧と式の意味付けの prompt を照合し、前提の欄が対象外であることを確かめる。
generalization:
  level: repo_pattern
  general_form: "上流で決まった生成言語の文を下流の面がそのまま出すしかなく、下流で直す手段が出所の明示・折りたたみに限られる"
pattern: upstream-loss-masked-downstream
discovery:
  perspective: [reproduction]
  note: "第 21 周のペルソナ通し受講（c-simulation-lab）で、全場面で前提が英語の長い列として出た。表示関数を辿ると前提は生成文をそのまま運んでいるだけで、表示側に言語を変える材料が無い。"
resolution:
  perspective: [deferred_decision]
  note: "据え置き。表示側は出所（解析結果・英語）を添えて先頭 1 件 + 折りたたみにする（第 2 波・UI）。上流の言語対象化は実測後の設計判断。"
  landed_in: []
related: [IK-0609]
view_of: []
history: []
---

## 課題

症状: 前提の欄に英語の長い文が並ぶ。原因（仮説）: 前提は解析段が英語で書き、生成言語の指定がこの欄に及ばない。下流の表示は受け取った文を運ぶだけなので、表示側では日本語にできない。この記録は「表示側では直せない」こと自体を残すためのもの。

## 発見の観点

ペルソナの通し受講（reproduction）で観測し、表示関数に言語を変える材料が無いことを確認した。

## 解決の観点

表示側で翻訳・要約を足すと、根拠の無い言い換えになり出所の正直さを崩すので採らない。表示は出所を名指して畳むに留め、上流（式の意味付けの生成言語）を対象に含めるかは、言語対象化の効果と LLM 回数の実測を見てから判断する。

## 一般化

上流で落ちた・固定された性質を下流の各面で補おうとすると、面を足すたびに同じ症状が出る。型は upstream-loss-masked-downstream。確かめ方は、同じ論文を生成言語の指定を変えて上流から通し、前提の欄の言語が変わるかを比べること。
