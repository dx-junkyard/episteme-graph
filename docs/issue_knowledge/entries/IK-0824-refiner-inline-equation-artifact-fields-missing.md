---
id: IK-0824
title: "component の子振り分けの材料で、inline 式候補の artifact だけ summary / latex / plain_text / source_evidence_ids が欠けて role しか渡らず、振り分けられずに unassigned になる"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "親部品に結びついた式・主張を子部品へ振り分けるため、各 artifact の要約・式本文・根拠を LLM に見せる"
  layers: [pipeline_a, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    仮説: 子振り分けの材料を組むとき、display 式は equation_semantics の record（summary・latex・evidence）から投影するが、inline 式候補
    （eq_eqcand_inline_*）は別の経路で ID と role だけを投影し、record の本文を引いていない。接続軸 information = inline 式の中身が振り分けの
    材料に届かない。処理軸 none / 構造軸 none / 統制軸 none。inline 式が定義の正本である場合（α⊥・α∥・A_AP）、主張の TeX 文で代替され、
    原文の ≡ が = に変わる。確定には refiner の artifacts_to_route を組む関数と、inline 候補の record の保存先を読む。
generalization:
  level: general
  general_form: "2 種類の項目を同じ材料に投影するとき、片方の種類だけ本文の欄を引かないため、その種類は ID だけで渡り判断に使えない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 Z2 が子振り分け（2610.08751・seq 3629 = comp_007・3620 = comp_001・3632 = comp_008）を読み、inline 式 eq_eqcand_inline_tex_b73_6_05ec97fa（A_AP の定義）だけ summary / latex / plain_text / source_evidence_ids が欠け unassigned にせざるを得ないこと、α⊥・α∥ の定義の inline 式が振り分け対象に無く主張の TeX 文（≡ が = に変化）で代替されていることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md）。頭脳 T2（uxsim/runs/radar_loop/BRAIN_NOTES_T2.md seq 2936）と W0（BRAIN_NOTES_W0.md seq 3285）も同じ症状を記している。"
resolution:
  perspective: [pending]
  note: "未解決。inline 式候補も equation_semantics の record から summary・latex・evidence を投影し、display 式と同じ欄で渡す案。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0813, IK-0810, IK-0728]
view_of: []
history: []
---

## 課題

症状: 子振り分けの材料で、inline 式候補 eq_eqcand_inline_tex_b73_6_05ec97fa（A_AP の定義）だけ summary / latex / plain_text / source_evidence_ids が欠け、role しか渡らないため unassigned になる。
display 式は揃っている。α⊥・α∥ の定義の inline 式は振り分け対象に無く、atomic 主張の TeX 文（原文の ≡ が = に変化）で代替される。
原因（仮説）: inline 式候補の投影が別経路で、本文の欄を引いていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3620・3629・3632）。

## 発見の観点

振り分けの artifact の欄を、display 式と inline 式で比べた（data_inspection）。

## 解決の観点

未解決。

## 一般化

片方の種類だけ本文を投影しないと、その種類は判断に使えない。辞書の型 available-but-unwired。
