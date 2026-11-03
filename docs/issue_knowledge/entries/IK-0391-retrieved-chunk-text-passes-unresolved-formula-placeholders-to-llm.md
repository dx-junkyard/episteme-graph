---
id: IK-0391
title: "学習チャットの検索が返すチャンク本文に [[FORMULA_N]] のプレースホルダーが解決されないまま残り、chunks.formulas に LaTeX があるのに LLM は尋ねられた式を読めなかった"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が論文の式について質問し、AI が式の中身に基づいて答える
  layers: [rag_chat, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸で、チャンカーは本文中の式を [[FORMULA_i]] に置き換えて LaTeX を chunks.formulas に分けて保存するが、
    search_chunks_with_metadata は c.text だけを SELECT し formulas を読まず、学習チャットの 2 つの組み立て箇所も
    本文をそのまま [出典N] に入れていた（information: 値は同じ行にあり、運ばれていなかった）。構造軸は medium:
    本文と式を分けて持つ表現自体はレクチャー・音声で使われており、問題は読み手側の結合の欠落。確認: 第 8 周の
    実プロンプトで出典3/4/5 に [[FORMULA_0]]…[[FORMULA_4]] が並び、1 つの式は行ごとに割れていた。
generalization:
  level: repo_pattern
  general_form: 本文と、本文が参照する別欄の値を分けて保存しているのに、読み手が本文だけを取り出して参照を解決せずに次段へ渡す
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: 第 8 周の実プロンプトに載った出典本文を読んだ。
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    検索の同じ SELECT に c.formulas を足し、結果の組み立てで resolve_formula_placeholders が [[FORMULA_i]] を
    $<latex>$ に置き換える（id 一致を優先・無ければ位置。LaTeX は制御文字を除き 1 行に畳む。空・抽出エラーの目印・
    対応なしは「（数式）」）。解決前の本文は raw_text に残す。learning.py は text を読むだけなので変更なしで
    両方の組み立て箇所に効く。レクチャーの検索経路（_generate_sequence_from_search）は LLM に渡す本文に
    式が入るようになり、LLM 失敗時の縮退（_fallback_spoken_text）は $...$ を再びプレースホルダー化する。
  landed_in:
    - backend/api/services.py
    - backend/tests/test_search_visibility.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場で式について尋ねたときの実プロンプト（出典本文に LaTeX が入り、プレースホルダーが残らないこと）
      - 出典ポップアップ・quote の 80 字切り詰めが $...$ の途中で切れる場合の表示（learning.py 側・未変更）
      - PDF 由来で LaTeX 化が粗い式（数式の信頼連鎖 E-1。復元式の印は付けていない）
related: [IK-0390]
view_of: []
history: []
---

## 課題

検索で返るチャンク本文の [[FORMULA_N]] が解決されず、LLM が式を読めなかった。

## 発見の観点

実プロンプトの出典本文を読んだ。

## 解決の観点

同じ行にある formulas を同じ SELECT で読み、結果の組み立てで LaTeX に解決する（元の本文は raw_text）。

## 一般化

本文と参照先の値を分けて持つのに、読み手が参照を解決せずに渡す。
