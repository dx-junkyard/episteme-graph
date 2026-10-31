---
id: IK-1016
title: "要旨・結論で本文の結果式（eq_r14 の M_max・R_{1.4} などの値）を文中で再掲した inline 式が、equation_semantics の inline 候補として本文の式とは別の式として立ち、同じ結果が複数の式に分かれる（基準値 c_s^2=1/3 も式として拾われる）"
status: open
recorded_at: 2026-10-27
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "論文の式を一意に同定し、結果式を主張・部品・導出の材料にする"
  layers: [pipeline_a, knowledge_objects]
classification:
  axes:
    processing: [input_handling]
    structure: [decomposition]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: inline 候補の抽出は文中の等式を一律に式の候補にし、本文の表示式と同値の再掲かどうかを見ない。equation_semantics のスキーマにも
    「同じ結果の再掲」を表す欄が無い（input/output は導出の向きだけ — 頭脳 B 4215）。観察: 頭脳 A 4214（要旨の inline 結果式が label 空・
    section_id None で eq_r14 と同値の別式）・4222（結論の再掲が eq_r14 と別式）、頭脳 B 4221（交差の基準値 c_s^2=1/3 が inline 式として
    候補化 — IK-0762 の値指定と同じ形）。処理軸 input_handling = 再掲・基準値の見分け。構造軸 decomposition = 何を 1 つの式と数えるか。
    接続軸 none（確信度 low: 後段の重複表示までは見ていない）。統制軸 none。
    確定には、inline 候補の抽出規則と、同じ document の表示式との値の照合の有無を読む。
generalization:
  level: general
  general_form: "同じ内容の再掲を出現ごとに別の対象として登録し、正本と再掲の区別が無いため 1 つの結果が複数の対象に分かれる"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "第 6 周の前の PINN TeX 再解析で、製品頭脳 A / B が equation_semantics の inline 候補の要求に本文の結果式の再掲・基準値が並ぶことを所見に残した（出所: uxsim/runs/round25/BRAIN_NOTES_product_A_r6pre.md 4214・4222、BRAIN_NOTES_product_B_r6pre.md 4215・4221）。"
resolution:
  perspective: [pending]
  note: "未解決。inline 候補を表示式と照合し、同値の再掲は新しい式にせず本文の式への言及として結ぶ。比較の基準値・値指定は式の候補にしない（IK-0762 の是正の範囲を確かめる）。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0762, IK-1014]
view_of: []
history: []
---

## 課題

症状: 要旨・結論の文中の結果値が、本文の結果式と別の式として equation_semantics の候補になる。単位（MeV）が inline 式の外に出て
値だけの式になるもの（4219）、交差の基準値 c_s^2=1/3 が式として拾われるもの（4221）もある。

原因（仮説）: inline 候補の抽出が再掲・基準値を見分けず、表示式との同値を照合しない。

出所: uxsim/runs/round25/BRAIN_NOTES_product_A_r6pre.md・BRAIN_NOTES_product_B_r6pre.md。

## 発見の観点

inline 候補の要求を本文の表示式と突き合わせた（data_inspection）。

## 解決の観点

未解決。正本の式と再掲の言及を分ける。

## 一般化

出現ごとに登録すると再掲が別の対象になる。型 unit-of-work-undefined（何を 1 つの式と数えるか）。IK-0762 と同族。
