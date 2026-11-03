---
id: IK-0730
title: "landscape_placement に渡す主張 20 件が序論の背景文に偏り、結果の主張が 1 件も無いまま分野の地図へ配置される"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/features/knowledge_landscape_design.md"
  - "docs/pipeline/overview.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "論文を分野の地図のアンカーへ、論文の問い・命題・主張を根拠に配置する"
  layers: [pipeline_a, knowledge_landscape]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [budget]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 配置の入力に載せる主張を、主張の種別・論理上の役割ではなく出現順（または上限 20 件の先頭）で選んでいる。
    処理軸 logic = 選別の基準が「先に出た主張」になっており、結果の主張（シミュレーション・パラメータ回復）が選ばれない。
    接続軸 information = 論文の結果に当たる情報が配置の段に届かず、数値シミュレーション領域に置く根拠が欠ける。
    統制軸 budget = 件数の上限が選別を強めている可能性（低確信）。構造軸 none（低確信: 選別の責務が配置側か
    claim 側か未確認）。確定には landscape_placement の入力 builder で主張を並べる順と上限を読み、序論・結果の
    構成比を実データで数える。
generalization:
  level: general
  general_form: "判断の根拠に載せる項目を、内容の役割ではなく並び順と件数上限で選ぶため、先頭に多い背景の項目だけが根拠になり、結論に当たる項目が落ちる"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 M0 が landscape_placement（seq 2130・EFTofLSS トライスペクトル）の材料を読み、claims 20 件が PNG・インフレーション・宇宙論的コライダーの背景文に偏り結果の主張が無いことに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_M0.md）。cosmology 領域の concept_slots_remaining=0 で EFTofLSS 系の概念を gap に出す余地も無かった。"
resolution:
  perspective: [pending]
  note: "未解決。主張の選別を claim_type / 論理ブロック（thesis の支持構造・結果）で層化し、結果の主張を必ず含める案。上限は stratified_sampling と同じ層化で切る。"
  landed_in:
    - "docs/features/knowledge_landscape_design.md"
related: [IK-0731, IK-0767]
view_of: []
history: []
---

## 課題

症状: landscape_placement の入力 claims 20 件が序論の背景文に偏り、結果の主張が 1 件も無い。6 配置のうち PNG 方面の配置は
動機文だけが根拠で、numerical_simulation に置けない。
原因（仮説）: 主張の選別が出現順と件数上限で、内容の役割を見ていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_M0.md（seq 2130）。

同根の再発: uxsim/runs/radar_loop/BRAIN_NOTES_Q1.md（seq 2569）— claims が文書冒頭の 20 件で打ち切られ、結果（ハローの割合・降着・銀河団スケール）の主張が素材に無く、galaxy_clusters 等の配置根拠を逐語で取れない。地図側の概念枠 0 は IK-0767 に分けた。

追加の出所（2609.35757・頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2960 — claims 素材が序論〜§2 の 20 件だけで、後半の SPCF 応答・HOD・DESI 応用が入らず method / application の観点の根拠が引けない）。

追加の出所（2610.08751・頭脳 X2・是正前の run（seq 3290〜3314））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3302 — claims 20 件がすべて序論・方法で、主結果（Mpc 単位で PVE が消える）の主張が入らず central_thesis だけが結果を運ぶ。particle_physics の eft_sumrules と「EFT」の語が字面で当たる誤配置の罠もある）。

追加の出所（2610.08751・頭脳 Z2）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3674 — claims が論文先頭から 20 件で打ち切られすべて要旨・序論。中核の結果が 1 件も無い。particle_physics が分野未指定の論文にも毎回並ぶ）。

## 発見の観点

配置の入力の主張と、論文の論理ブロック（結果）を突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

根拠の選別を並び順で決めると、先頭に多い背景だけが根拠になる。辞書の型 extent-decided-by-proxy-signal。
