---
id: IK-0826
title: "claim_qualification の adjacent_equations がブロックの先頭 span（前の式）と末尾 span（後の式）にしか付かず、同じブロックの中間の span が「In the second line, …」と式の行を参照しても式が届かない"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "主張採否で、式を参照する span（where 節・導入句・式の行の名指し）に隣接する式を添えて参照先を読めるようにする"
  layers: [pipeline_a]
classification:
  axes:
    processing: [logic]
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
    仮説: IK-0714 の是正で、隣接式を渡す条件を「ブロック先頭の span で続き語か直前の式の名指しで始まる（前の式）」「ブロック末尾の span で
    文末記号で閉じていない（後の式）」の位置で決めた（refers_to_preceding_equation / ends_as_equation_intro）。式を参照するかどうかを span の
    位置で代理しているため、ブロックの中間の span が「In the second line」のように式の行を参照しても式が添えられない。処理軸 logic = 添付の
    条件が位置に依存する。接続軸 information = 中間 span には式が渡らない。構造軸 none / 統制軸 none。確定には両判定関数の呼び出し条件
    （span の位置）を読む。
generalization:
  level: general
  general_form: "参照先を添える条件を要素の位置で代理するため、位置の条件に当たらない要素が同じ参照をしても参照先が渡らない"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 AA2 が 2609.35757 の再解析 run 54e85a2a の claim_qualification（seq 3794 = tex_b125 span_002「In the second line」）で式が届かないことに気付き、req 3727（span_005 = after）・3728（span_001 = before）で付く位置を確かめた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md）。seq 3746・3764 の中間 span にも区画が無い。"
resolution:
  perspective: [pending]
  note: "未解決。隣接式はブロックの全 span に付けるか、式の行・式を名指しする句（the second line / this expression / Eq.）を位置によらず判定して付ける案。LLM 呼び出し数は不変。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0714, IK-0822]
view_of: []
history: []
---

## 課題

症状: 再解析 run の claim_qualification で、adjacent_equations の区画はブロックの先頭 span（前の式）と末尾 span（後の式）にだけ付き、tex_b125 span_002 の「In the second line, …」のような
中間の span には付かない。参照される式の行が材料に無く evidence weak になる。
原因（仮説）: 隣接式を渡す条件が span の位置で決まっている（IK-0714 の是正の範囲）。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（seq 3794・3746・3764。位置の確認は req 3727・3728）。

## 発見の観点

隣接式の区画が付く span の位置と、式を参照する句を持つ span を突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

参照の有無を位置で代理すると、位置に当たらない参照が取りこぼされる。辞書の型 extent-decided-by-proxy-signal。
