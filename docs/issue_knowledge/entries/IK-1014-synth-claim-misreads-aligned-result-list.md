---
id: IK-1014
title: "式由来の合成主張が、aligned 環境に並べただけの結果の一覧（M_max・R_{1.4}・ρ0・c_s^2 の値）を 1 本の関係式と読み、「R_{1.4} depends on c_s^2, ρ0, M_max」「eliminate M_max, P, R_{1.4}, ρ0 while retaining c_s^2」のような無根拠の主張を作り、ComponentRefiner がそれに従って音速の定義の部品を op1〜op3 に機械分割する"
status: open
recorded_at: 2026-10-27
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/theory-graph.md"
feature_context:
  realizing: "式から決定論で合成した主張を、部品の組み立てと子部品への分割（理論操作グラフ）の材料にする"
  layers: [pipeline_a, theory_artifacts]
classification:
  axes:
    processing: [input_handling]
    structure: [decomposition]
    connection: [meaning]
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
    仮説: equation_claim_synthesis は 1 つの表示式環境を 1 本の式として扱い、左辺・右辺の記号から依存・消去の主張を組む。aligned に
    独立した結果を 1 行ずつ並べた環境（`M_max &= …\\ R_{1.4} &= …`）も 1 本の連立として読まれ、互いに無関係な結果の間に依存・消去の
    関係が作られる。ComponentRefiner は合成主張 synth_claim_0006 の「eliminate … while retaining c_s^2」を操作の単位として受け、
    comp_007（音速の定義 1 操作）を op1〜op3 に分割し、op2「Derive」が結果式 eq_r14 を出力、op3「Evaluate condition: M_max, P, R_{1.4}」を
    作った（op の summary は元の文の複製・op1 から comp_001 への依存が落ちる）。処理軸 input_handling = 環境の中の行の数え方。
    構造軸 decomposition = 何を 1 本の式・1 つの操作と数えるかが定まっていない。接続軸 meaning = 並置が関係として読まれる。統制軸 none。
    確定には、合成器が aligned の行をどう分けるか（行ごとの独立性を見るか）を読み、eq_r14 の TeX と synth_claim_0002 / 0006 の材料を突き合わせる。
generalization:
  level: general
  general_form: "書式上の並置（同じ環境に並べた独立の結果）を意味上の関係と読み、無い関係を前提に後段が単位を分割する"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "第 6 周の前の PINN TeX 再解析で、製品頭脳 B が component_assembly の入力（4225）で合成主張の誤読を、辺推定（4227）で refiner の機械分割を所見に残した。頭脳 A も同じ再解析で結果の重複（4222）を見た（出所: uxsim/runs/round25/BRAIN_NOTES_product_B_r6pre.md）。"
resolution:
  perspective: [pending]
  note: "未解決。aligned / align の行を 1 本ずつの式として扱い、行どうしに記号の共有が無ければ関係の主張を合成しない。合成主張を refiner の分割の根拠にするのは、本文由来の主張か式間の導出が裏付けるときに限る。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0728, IK-0798, IK-0762, IK-1016]
view_of: []
history: []
---

## 課題

症状: component_assembly の入力に「R_{1.4} depends on c_s^2, ρ0, M_max」「eliminate M_max, P, R_{1.4}, ρ0 while retaining c_s^2」のような
合成主張が並び、ComponentRefiner が音速の定義の部品を「Define / Derive / Evaluate condition: M_\mathrm{max}, P, R_{1.4}」の 3 つに分けた。
分割後の「Evaluate condition」は見出しと本文が食い違い、二層説明は説明できずに skipped になった（4231）。

原因（仮説）: 結果を並べた aligned 環境を 1 本の関係式として合成主張を作った。

出所: uxsim/runs/round25/BRAIN_NOTES_product_B_r6pre.md（4225・4227・4231）。

## 発見の観点

refiner の子部品と、その根拠に挙がった合成主張と、元の TeX を突き合わせた（data_inspection）。

## 解決の観点

未解決。式の単位（環境ではなく行）を合成の前に決める。

## 一般化

書式の並びを意味の関係と取り違えると、後段は無い関係を単位にして分割する。型 unit-of-work-undefined（IK-0762 の inline 値と同族）。
