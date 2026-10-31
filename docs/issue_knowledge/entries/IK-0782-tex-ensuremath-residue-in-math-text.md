---
id: IK-0782
title: "TeX 取り込みで論文のマクロ（\\ev 系）の展開結果 `\\ensuremath{\\left\\langle …\\right\\rangle}` が式テキスト・本文・逐語引用にそのまま残り、trusted として渡る式と学習者向け表示に伝播する"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/overview.md"
  - "docs/features/url_material_upload_design.md"
feature_context:
  realizing: "TeX 原文の論文マクロを展開し、式と本文を KaTeX で描ける LaTeX と平文にして後段に渡す"
  layers: [pipeline_a, url_material_fetch]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: tex_archive は地の文で使われた引数なしマクロ（\RD → \ensuremath{R_D}）だけは _text_mode_macro_math が \ensuremath を剥がして
    数式として退避するが、引数を取るマクロ（\ev{S} 等）や数式の中で使われたマクロは _expand_simple_macros の展開結果をそのまま残し、
    \ensuremath{…} が剥がされない。処理軸 input_handling = マクロ展開の後処理（\ensuremath の除去）が一部の入口にしか無い。
    構造軸 none = 表現の受け皿の問題ではない。接続軸 none = 残骸は値として後段に正しく渡っている（渡り方ではなく中身の問題）。
    統制軸 none。確定には、2609.35757 の \ev 系マクロの定義と、_expand_simple_macros を通した式テキストを比べる。
    KaTeX が \ensuremath を解釈しない場合は表示が崩れる（未確認）。
generalization:
  level: repo_pattern
  general_form: "ソースのマクロ展開で生じた補助命令の除去を一部の入口にだけ置き、他の入口から展開した文字列に補助命令が残って後段と表示へ流れる"
pattern: rule-enforced-per-surface
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で、2609.35757（TeX 経路）の材料を頭脳 S1（seq 2773・2821・2833・2842）と S2（seq 2774・2834・2837・2846）が読み、claim_qualification の span 本文・Source Block・逐語の evidence_quote と、equation_semantics の trusted TeX として渡る Equation Text の両方に \\ensuremath{\\left\\langle …\\right\\rangle} が残っていることを見た。出所: uxsim/runs/radar_loop/BRAIN_NOTES_S1.md・uxsim/runs/radar_loop/BRAIN_NOTES_S2.md。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "tex_archive に _unwrap_ensuremath（入れ子対応で \\ensuremath{X} → X）を置き、表示式とインライン数式の正規化の 2 箇所から呼ぶ。原本の該当行（\\ev 系マクロの展開）で残骸が消えることを確認し、test_latex_display.py の 2 クラス（TestTexIngestUnwrapsEnsuremath ほか）で固定した。"
  landed_in:
    - "backend/core/document_pipeline/tex_archive.py"
    - "backend/tests/test_latex_display.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "部品①（除去の正本を 1 関数にする）を採り、数式の 2 つの正規化経路から呼ぶ結線にした。全経路を走査する横断検査（部品③）は置いていない。設計前に原理は読んでいない。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 LLM での 2609.35757 の再演（再取り込み後の式テキスト・span に残骸が無いこと）"
related: [IK-0696, IK-0766]
view_of: []
history: []
---

## 課題

症状: 式テキスト（equation_semantics の Equation Text）・本文ブロック・claim の span と逐語引用に `\ensuremath{\left\langle S \right\rangle}`
のような展開残骸が残る。逐語の evidence_quote は残骸を含まざるを得ず、学習者向けの表示まで伝播しうる。

原因（仮説）: \ensuremath の除去が地の文の引数なしマクロの経路にしか無く、引数付きマクロや数式内のマクロの展開結果には効いていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_S1.md（seq 2773・2821・2833・2842）、uxsim/runs/radar_loop/BRAIN_NOTES_S2.md（seq 2774・2834・2837・2846）。

追加の出所（頭脳 S0）: uxsim/runs/radar_loop/BRAIN_NOTES_S0.md（seq 2829・2835・2838 — Next Span・span 本文・式本文に \ensuremath{\left\langle …} の残骸）。

追加の出所（頭脳 T1）: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2866・2872・2908・2947 — 旧 document c795b2cf の材料に \ensuremath{…} と \sigma_uv^2(M) のマクロ展開残りが残る。seq 2986 — 再取り込みの document 2a24b9e4 では \left\langle … のまま \ensuremath 包みが無く、是正が効いている様子）。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2871 — 是正前の document c795b2cf で ensuremath 残骸。seq 2982 — 再取り込みの document 2a24b9e4 では前の式に ensuremath 残骸が無く、是正が効いている）。

## 発見の観点

主張採否と式の意味付けの入力を実データで読んだ（data_inspection）。

## 解決の観点

\ensuremath の除去を 1 関数にまとめ、表示式とインライン数式の正規化経路から呼んだ（single_point_fix）。回帰テストで固定した（guardrail_fix）。

## 一般化

展開の後処理を入口ごとに置くと、新しい入口から残骸が漏れる。確かめ方は、引数付きマクロが \ensuremath に展開される TeX を通し、
式テキスト・本文・span のどこにも \ensuremath が残らないことを見る。辞書の型 rule-enforced-per-surface（IK-0766 のラベル残骸・
IK-0696 の環境名残骸と同じ「取り込みの残骸」の系列）。
