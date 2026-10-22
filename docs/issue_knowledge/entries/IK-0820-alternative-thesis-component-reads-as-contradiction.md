---
id: IK-0820
title: "中心命題の別定式化（「h そのものが主因」）が部品 comp_015 として「結果」の役割で並び、中心命題（主因は単位の選択）と字面で衝突して読める。本文の区別（事前分布を課すパラメータ vs 名目の単位系）が中心命題・部品に伝わらない"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/graph_paper_layer_design.md"
feature_context:
  realizing: "論文の中心命題と別の定式化を再構成し、それを支える部品・説明に中心命題との関係を添えて渡す"
  layers: [pipeline_a, hierarchical_explanation]
classification:
  axes:
    processing: [none]
    structure: [representation]
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
    仮説: thesis_reconstruction の alternative_theses と部品の role_in_thesis に、中心命題との関係（対立・精密化・言い換え）を表す欄が無く、
    別定式化を支える主張が部品になると一律「States a result the thesis asserts.」の定型文で結果として並ぶ。論文自身は tex_b115 で
    「フィットに使う単位系ではなく、事前分布を実際に課すパラメータ（h）が問題」と区別しており、両者は精密化の関係だが、その区別が
    中心命題の文にも部品にも乗らない。構造軸 representation = 中心命題と別定式化・部品の関係の種類を表す欄が無い。接続軸 meaning =
    精密化の関係が後段で「別の結果」として読まれ、学習者には矛盾に見える。処理軸 none / 統制軸 none。確定には thesis_reconstruction の
    出力スキーマ（alternative_theses の欄）と、component_assembly が role_in_thesis を組む規則を読む。
generalization:
  level: general
  general_form: "主命題と別の定式化の関係の種類を表す欄が無いため、精密化にあたる定式化が独立した結果として並び、主命題と矛盾して読まれる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 X2 が contextual_explanation（2610.08751・是正前の run・seq 3299）で comp_015「h, not the unit choice itself, primarily drives the projection effects.」が role_in_thesis「States a result the thesis asserts.」で中心命題と字面で衝突することに気付き、再解析 run の役割判定（seq 3371・tex_b115）でその衝突を解く本文の区別（名目の単位系 vs 事前分布を課すパラメータ）を確かめた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md）。頭脳 W1 も中心命題と結論の緊張を記していた（uxsim/runs/radar_loop/BRAIN_NOTES_W1.md seq 3220）。"
resolution:
  perspective: [pending]
  note: "未解決。alternative_theses と部品の role_in_thesis に中心命題との関係の種類（精密化・対立・言い換え）と根拠の逐語を持たせ、定型文の role_in_thesis をやめる案。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0755, IK-0753]
view_of: []
history: []
---

## 課題

症状: 部品 comp_015「h, not the unit choice itself, primarily drives the projection effects.」が role_in_thesis「States a result the thesis asserts.」で、中心命題（PVE の主因は単位の選択）と字面で衝突する
「結果」として並ぶ。論文自身は tex_b115 で「フィットに使う単位系ではなく、事前分布を実際に課すパラメータが問題」と述べ、両者は精密化の関係。
その区別が中心命題・部品に伝わらず、学習者は矛盾と読む恐れがある。付録の限定（Mpc でも残る c_nlo の PVE、seq 3398）も中心命題の限定として拾われるべき材料。
原因（仮説）: 中心命題と別定式化・部品の関係の種類を表す欄が無い。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3299・3371・3398）。

追加の出所（頭脳 Y2・再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3500・3503 — 結論ブロックで「h そのものが主因で単位の選択そのものではない」の atomic 主張が単独では headline と字面で衝突し、中心命題との関係（精密化）は qualification_reason にしか書けない。headline_claim 自体は両者を正しく両立させている）。

## 発見の観点

部品の主張と role_in_thesis を、中心命題と本文の要約文（tex_b115）に突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

主命題と別定式化の関係を表さないと、精密化が矛盾に見える。辞書の型 information-dropped-as-unrepresentable。
