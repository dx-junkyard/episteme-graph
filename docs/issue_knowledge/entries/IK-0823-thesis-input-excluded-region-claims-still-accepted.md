---
id: IK-0823
title: "thesis_reconstruction の入力で、excluded_regions が除外した付録 tex_b156 の主張が accepted_claims に残り、logic_2 の根拠ブロックが除外済みの tex_b34 を指す一方、頑健性の節（BAO・RSD 模型・DESI 型）の主張は 1 件も入らない"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "論文の骨格（除外する領域・論理ブロック）と採択された主張を揃えて、中心命題と支持構造の再構成の入力にする"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
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
    仮説: thesis_reconstruction の input_builder が、paper_skeleton の excluded_regions と logical_blocks を表示用に添えるだけで、
    accepted_claims の選別（と logical_blocks の根拠ブロック）にその除外を適用していない。同時に accepted_claims の選び方が節の網羅を
    見ないため、頑健性の節の主張が 0 件になる。接続軸 condition = 前段の除外の条件が後段の選別に伝わらない。処理軸 none / 構造軸 none /
    統制軸 none。component_assembly の採択主張の節の偏り（IK-0815）と同じ選別の問題が thesis の入力にもある可能性。確定には
    thesis_reconstruction の input_builder が accepted_claims と logical_blocks の evidence を何から組むかを読む。
generalization:
  level: general
  general_form: "前段が決めた除外の条件を後段の選別に適用しないため、除外した領域の項目が入力に残り、残すべき領域の項目は選別の偏りで抜ける"
pattern: condition-not-propagated
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 Z2 が thesis_reconstruction（2610.08751・seq 3608）の材料を読み、excluded_regions（付録 tex_b156）と accepted_claims（tex_b156:span_002 を含む）・logic_2 の evidence（除外済み tex_b34）の食い違い、頑健性の節の主張がゼロであることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md）。同じ材料で eq_tex_b78 が「could not be inferred」の unknown のまま major_equations に入る。"
resolution:
  perspective: [pending]
  note: "未解決。excluded_regions を accepted_claims と logical_blocks の根拠ブロックの選別に適用し、主張の選別は節ごとに枠を配って結果・頑健性の節を落とさない（IK-0815 と同じ規律）案。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0815, IK-0722]
view_of: []
history: []
---

## 課題

症状: thesis_reconstruction の入力で、excluded_regions が付録として除外した tex_b156 の主張（tex_b156:span_002）が accepted_claims に残り、logic_2 の根拠ブロックが除外済みの tex_b34 を指す。
一方で頑健性の節（BAO・RSD 模型・DESI 型）の主張は accepted_claims に 1 件も無く、logic_6 だけが運ぶ。
原因（仮説）: 除外の条件が主張と根拠ブロックの選別に伝わらず、主張の選別が節の網羅を見ていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3608）。

## 発見の観点

thesis の入力の excluded_regions・logical_blocks・accepted_claims を相互に突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

前段の除外を後段の選別に伝えないと、除外したものが残る。辞書の型 condition-not-propagated。
