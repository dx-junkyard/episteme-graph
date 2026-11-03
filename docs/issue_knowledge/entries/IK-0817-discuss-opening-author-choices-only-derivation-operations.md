---
id: IK-0817
title: "discuss_opening の材料で author_choices（著者の手）8 件がすべて Formalism の式操作（define / substitute）で、論文の核心の選択（単位の選択・TCM）が 1 件も無く、from / to も生 TeX の「…」切り詰めで引用に使えない"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/features/discuss_opening_authoring_design.md"
feature_context:
  realizing: "論文の著者が選んだ手（方法・仮定の選択）を材料に、discuss 開幕の「議論のきっかけ」を生成する"
  layers: [discuss, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [completion]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: discuss_opening の入力 builder が「著者の手」を derivation の operation 列から作っており（設計書の入力 = derivation operation 列）、
    方法の選択として本文で述べられた選択（rhetorical_role の method_choice・claim の方法主張）を材料に入れない。統制軸 completion =
    「著者が選んだ手」を式操作の列で代理判定している。接続軸 information = 方法の選択の主張が開幕の材料に届かない。構造軸 none（low）/
    処理軸 none。確定には input_builder が author_choices を何から組むかと、from / to の切り詰めの規則を読む。
generalization:
  level: general
  general_form: "人が下した選択を、記録の取りやすい機械的な操作の列で代理するため、核心の選択が材料に入らず細部の操作だけが並ぶ"
pattern: completion-defined-by-proxy
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 X1 が discuss_opening（2610.08751・是正前の run・seq 3301）の材料を読み、author_choices 8 件がすべて Formalism の define / substitute で、単位の選択・TCM の手が無く、from / to が生 TeX の末尾「…」で切れ、paper_goal に「VDG$_\\infty$」の TeX 片が残ることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md）。同じ材料の untested_assumptions の半分が序論の一般論である件は IK-0752。"
resolution:
  perspective: [pending]
  note: "未解決。著者の手の材料に method_choice の主張（と人が確定した前提）を加え、式操作は補助に回す。from / to は式ラベル（印字番号）で示し生 TeX を切らない案。"
  landed_in:
    - "docs/features/discuss_opening_authoring_design.md"
related: [IK-0752, IK-0808]
view_of: []
history: []
---

## 課題

症状: discuss_opening の author_choices 8 件がすべて Formalism の式操作（define / substitute）で、論文の核心（単位の選択・TCM）の手が 1 件も無い。from / to は生 TeX の末尾「…」切り詰めで、
学習者向けの引用に使えない。
原因（仮説）: 著者の手を derivation の operation 列で代理し、本文で述べた方法の選択を材料にしていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3301。是正前の run）。

追加の出所（頭脳 Z2）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3671 — author_choices の operation が定義・分解にも「approximate」と付き（eq_tex_b37 だけ define）、「著者が選んだ近似」と読めてしまう）。

## 発見の観点

開幕の材料の author_choices を、論文が中心に据える方法の選択と突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

人の選択を機械的な操作の列で代理すると、核心が抜けて細部が並ぶ。辞書の型 completion-defined-by-proxy。
