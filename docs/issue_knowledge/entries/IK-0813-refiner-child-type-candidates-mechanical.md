---
id: IK-0813
title: "component_assembly の子振り分けで、子の候補の型が機械的に並び、assumption 型が無いため仮定（分離可能性）の行き場が無く、式が 1 本しかない部品にも equation_system の子が立つ"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "親部品を責務（定義・導出・式の体系・模型・仮定）ごとの子に分け、親に結びついた式・主張を子へ振り分ける"
  layers: [pipeline_a, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [representation, decomposition]
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
    仮説: refiner が子の候補を親の component_type から決まった型の並び（definition / derivation / equation_system / model …）で
    立て、親が持つ式・主張の中身（仮定の有無・式の本数）を見ない。構造軸 representation = 子の型の語彙に assumption が無く、仮定の
    主張・式を表す子が作れない。構造軸 decomposition = 式 1 本の部品に式の体系の子を立てるなど、分け方が中身と合わない。接続軸 none /
    処理軸 none / 統制軸 none。確定には refiner が子の型の候補を決める表と、assumption を子の型に含めない理由を読む。
generalization:
  level: general
  general_form: "子の種類を親の種類から機械的に並べるため、中身に必要な種類が無く、中身の無い種類の子が立つ"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 W2 が子振り分け（2610.08751・seq 3284 = comp_002 ラプラス周辺化、seq 3287 = comp_010 TCM 再パラメータ化）を読み、分離可能性の仮定に当たる子の候補が無いこと、式 1 本の部品で equation_system の子が空のまま残ることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md）。頭脳 T1（uxsim/runs/radar_loop/BRAIN_NOTES_T1.md seq 2935）も assumption 系の子が無く主張が unassigned に落ちることを記している。"
resolution:
  perspective: [pending]
  note: "未解決。子の型の候補を親の式・主張の中身から決め（仮定の主張があれば assumption、式が 2 本以上のときだけ equation_system）、空の子を立てない案。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0728, IK-0792]
view_of: []
history: []
---

## 課題

症状: comp_002（ラプラス周辺化）の振り分けで子の候補に assumption 型が無く、分離可能性の仮定（式・主張）の行き場が無い。comp_010（TCM 再パラメータ化）は式が 1 本なのに
equation_system の子が立ち、空のまま残る。
原因（仮説）: 子の型の候補が親の型から機械的に決まり、中身を見ていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3284・3287）。

追加の出所（頭脳 X0・是正前の run（seq 3288〜3312））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3288 — comp_012 の子の候補が application / constraint の 2 つだけで、親の主張を支える定義式の置き場が無く、eq_chi2_marg のような定義式が必ず unassigned に落ちる。親と別主題の主張も振り分け対象に入る）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z0/Z1）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3621・3624・3627 — eq_tex_b39・b43・eq_post_laplace が親を跨いで再び振り分け対象になり、別々の子に入りうる。suggested_responsibility の model が Hessian の定義に付く）、uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3634 — 式を 1 本も持たない部品 comp_009 に equation_system の子候補が立ち、再判定の理由が全件同文）。

## 発見の観点

子の候補の型を、親の持つ式・主張の中身と突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

子の種類を機械的に並べると、要る種類が無く要らない種類が立つ。辞書の型 information-dropped-as-unrepresentable（表せない種類がある）。
