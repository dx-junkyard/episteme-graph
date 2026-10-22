---
id: IK-0766
title: "TeX 取り込みで式ブロックの頭に `label=eqn:halopressure` がそのまま残り、equation_semantics の近傍ブロック本文に式の一部として混入する"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/overview.md"
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "arXiv の TeX ソースを読み、式環境の本文から \\label などの組版指示を剥がして式ブロックとして後段へ渡す"
  layers: [pipeline_a]
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
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: tex_archive.py のラベル剥がしが `\label{eqn:…}` の形だけを想定し、`label=eqn:…`（オプション引数や
    マクロ展開後の形）を取りこぼして式ブロックの頭に残している。処理軸 input_handling = 取り込みの正規化漏れ。
    後段は残骸を式の本文として読み、LLM は材料に ID が無いためラベルから eq_eqn_* を推定する（IK-0757 の 2 形式混在に
    つながる）。構造・接続・統制の軸は該当なし。確定には tex_archive.py のラベル除去の正規表現と、該当論文の式環境の
    原文の書き方を確認する。
generalization:
  level: general
  general_form: "取り込みで剥がすべき組版指示を、想定した 1 つの書き方でしか剥がさないため、別の書き方の指示が本文に残って後段で内容として読まれる"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 Q1 が 2610.19001（TeX 経路）の equation_semantics 要求（seq 2488・P^l 線形回帰モデル）を読み、近傍ブロック本文の式の頭に 'label=eqn:halopressure' が混入していることに気付いた。出所: uxsim/runs/radar_loop/BRAIN_NOTES_Q1.md。"
resolution:
  perspective: [pending]
  note: "未解決。ラベルの除去を `\\label{…}` 以外の書き方（オプション引数・key=value 形）にも広げ、除去したラベルは式の ID の材料として別に保持する（情報は落とさない）。"
  landed_in:
    - "docs/pipeline/overview.md"
related: [IK-0696, IK-0697, IK-0757]
view_of: []
history: []
---

## 課題

症状: TeX 経路の式ブロックの頭に `label=eqn:halopressure` が残り、equation_semantics の近傍ブロック本文に式の一部として渡る。
材料に式 ID が無いため、LLM はラベルから eq_eqn_* を推定して入出力 ID を張る。
原因（仮説）: `backend/core/document_pipeline/tex_archive.py` のラベル剥がしが別の書き方のラベル指定を取りこぼしている。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_Q1.md（seq 2488）。

追加の出所（2609.35757・TeX 経路）: uxsim/runs/radar_loop/BRAIN_NOTES_S1.md（seq 2839 — 近傍の式ブロック本文に label=eqn:npoint 等が混入）、uxsim/runs/radar_loop/BRAIN_NOTES_S2.md（seq 2837・2849 — label=eqn:npoint・label=eqn:cluster が本文に混入）。

追加の出所（頭脳 S0）: uxsim/runs/radar_loop/BRAIN_NOTES_S0.md（seq 2838 — tex_b17 に「label=eqn:pairwise」の残骸）。

追加の出所（2609.35757・TeX 経路・頭脳 T1）: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2869 — 前文 tex_b113 の式に label=eqn:cfog が本文混入）。隣接の観察（seq 2881）: section_title に $P_{\rm stoch}$ の生 TeX が載り、見出しの表示で KaTeX 化されないと生で出る。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2871 — tex_b129 に「label=eqn:pbs-Rdelta」の残骸）。

追加の出所（頭脳 W0）: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3249 — Next Text Blocks に「[tex_b47] label=eq:post_laplace {…}」のラベル記法が本文に混入）。

追加の出所（頭脳 X0・全是正入りの再解析 run 40ecf8df（seq 3315 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3366・3396 — section_title に生 TeX「${\rm Mpc}/h$-」「$A_{\rm AP}$」が残る。$ の二重化は解消）。

残存の確認（頭脳 Y0/Y1/Y2・再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md（seq 3468）、uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3469・3511・3470・3509 — section_title に「${\rm Mpc}/h$-」「$\Lambda$CDM」「$A_{\rm AP}$」の生 TeX）。

残存の確認（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3570 — Next Context の式本体が $ 無しの生 TeX（\left\langle…）で渡る）。

残存の確認（頭脳 AA2・2609.35757 の再解析 run 54e85a2a）: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（seq 3833 — section_title に生 TeX「$W_p$」）。

## 発見の観点

LLM に渡った近傍ブロック本文と TeX の式環境を突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

TeX の組版の残骸が本文に残る系統で、IK-0696（レイアウト残骸の独立ブロック化）・IK-0697（参照除去の空残り）と同じ取り込みの
正規化の問題。辞書の型は IK-0696 にそろえて unit-of-work-undefined（何を本文の内容と数えるかの基準）。
