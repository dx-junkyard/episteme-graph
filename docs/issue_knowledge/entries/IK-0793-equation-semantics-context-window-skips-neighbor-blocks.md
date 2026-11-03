---
id: IK-0793
title: "equation_semantics の近傍文脈で、隣接する本文ブロックが系統的に欠ける（式 eq:S1 と eq:S2 の間の tex_b204、表の前の tex_b92）か、Previous Text Blocks が自ブロックだけになり、先行詞や使用先の式が材料に入らない"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "式ごとに前後の本文ブロックを添えて、式の意味役割と入出力の式を LLM に判定させる"
  layers: [pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: low
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: equation_semantics の入力 builder が近傍ブロックを集めるとき、空本文・接続語だけのブロックや表見出しのブロックを
    落とす、または inline 候補では自ブロックを「前」として扱い、本当の直前ブロックを窓から外す。接続軸 information =
    式の先行詞（「these constraints」の列挙・D/Dτ を使う漸化式）や間のブロックが材料に渡らない。処理軸 input_handling（low）=
    空・短いブロックの扱い。構造軸 none（low）/ 統制軸 none。確定には近傍ブロックの選び方（窓の数え方・除外条件）と、
    tex_b204・tex_b92 の本文・種別を読む。
generalization:
  level: general
  general_form: "近傍の窓を数えるときに短い・空の要素や自要素の扱いを決めていないため、隣接要素が系統的に窓から欠けて先行詞が材料に入らない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 T1 が equation_semantics（2609.35757・TeX 経路）の近傍文脈を読み、eq:S1 の次と eq:S2 の前の両方で tex_b204 が欠けること（seq 2893・2896）、inline 候補で Previous Text Blocks が自ブロックだけになり先行の列挙や漸化式が材料外になること（seq 2926・2929）、表見出しと思われる tex_b92 の欠落（seq 2929）、Next Text Blocks 区画そのものが無い例（seq 2908）に気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md）。"
resolution:
  perspective: [pending]
  note: "未解決。窓は文書順の隣接ブロックを空でも欠かさず数え（空なら空と明示）、inline 候補では自ブロックとは別に直前・直後のブロックを添える案。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0714, IK-0757]
view_of: []
history: []
---

## 課題

症状: 式 eq:S1 の Next Text Blocks が tex_b205 から始まり、eq:S2 の前文でも tex_b204 が欠ける（両式の間のブロックが系統的に落ちる）。
inline 候補（tex_b80・tex_b89）では Previous Text Blocks が自ブロックだけで、「these constraints」の先行の列挙や D/Dτ を使う漸化式が
材料外になり、頭脳は span から推定するか broken_context とした。表ブロック tex_b93 の前の tex_b92 も欠ける。
原因（仮説）: 近傍の窓の数え方が空・短いブロックと自ブロックの扱いを決めていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2893・2896・2908・2926・2929）。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2892 — tex_b204 の欠番。seq 2880 — tex_b147・b156・b158 の欠番（図ブロックか）。seq 2889・2922 — 前文脈 1 ブロックのみ・Next Text Blocks なし）。

追加の出所（頭脳 W0）: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3264 — Neighbor Equations が 2 件だけで、本文が「already defined in Eq. (14)」と参照する eq_multipoles（A_AP の定義元）が入らず入力を張れない。seq 3150 — ブロック先頭の span では前ブロックの照応先が Previous Span に無い）。

残存の確認（頭脳 Y0/Y1/Y2・再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3527・3529・3530・3524 — tex_b42（式の間の where 節）が Next Text Blocks から抜ける。seq 3551 — 表 1 の caption と思われる tex_b81 が抜け、表本体が caption なしで並ぶ）、uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md（seq 3525・3534 — 近傍の式リストに別節の eq_Pgg・eq_ctr_terms・AP の式まで混ざる。seq 3546 — 窓は式の位置を中心にずれており、近さの窓自体は効いている）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3576・3579 — Next Text Blocks が tex_b41 → tex_b43 と飛び、短い繋ぎのブロック tex_b42（b44 が「is the Hessian…」で始まるので「where」相当）が印なしで窓から落ちる。指示では新規候補だったが、本エントリ（短い・空のブロックが窓から系統的に欠ける）と同じ症状なのでここに記録）。

追加の出所（頭脳 Z2・2609.35757 の再解析 run 54e85a2a と 2610.08751 の再解析 run の後段）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3581 — tex_b42 が Previous Text Blocks から欠ける（4 件目）。seq 3587 — tex_b55（Fig.1 の caption か）が Next Text Blocks から欠ける）。

追加の出所（頭脳 AA2・2609.35757 の再解析 run 54e85a2a）: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（seq 3842 — 序論の唯一の式 eq_eqn_sigmaM の Neighbor Equations が sec_3 以降の 7 式だけで関係が無い。seq 3857 — 窓が前方へずれる例では近傍として機能。seq 3845 — Previous Text Blocks に式ブロックが本文と同列に混ざり、label の有無で書式が違う）。

## 発見の観点

equation_semantics の Previous / Next Text Blocks の block 番号の並びを文書順と突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

窓の要素の扱いを決めないと、隣接要素が黙って欠け先行詞が渡らない。辞書の型 information-dropped-as-unrepresentable の近傍。
