---
id: IK-0822
title: "claim_qualification でブロック先頭の span には Previous Span が無く、前ブロックの末文が渡らないため、「For this purpose」「these approaches」「these contributions」の照応が材料から解けない"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "主張採否と atomic rewrite で、span の照応（代名詞・指示語）を前後の文脈から解いて主張を自己完結させる"
  layers: [pipeline_a]
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
    仮説: claim_qualification の input_builder が Previous / Next Span を同じブロックの span からだけ取り、ブロック先頭の span では前ブロックの
    末文を渡さない。照応先は直前のブロック（多くは段落の切れ目の向こう）にあるので、atomic rewrite が主語を補えず hint や推測に頼る。
    接続軸 information = 前ブロックの末文が材料に届かない。処理軸 none / 構造軸 none / 統制軸 none。式を指す照応（IK-0714 の隣接式）とは
    別の、地の文どうしの照応の件。確定には Span Context の組み立てでブロック境界をまたがない条件を読む。
generalization:
  level: general
  general_form: "文脈の窓を処理単位（ブロック）の内側だけで組むため、単位の先頭の要素は単位の外にある照応先を受け取れない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 Y1 が再解析 run 40ecf8df の claim_qualification（2610.08751・seq 3457）で、節頭の span「For this purpose」の目的が前ブロックにあり Previous Span 欄が無いこと、同じブロックの span_004（seq 3460）には Previous Span があることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md）。頭脳 X2 も seq 3410（「these approaches」）・3434（「these contributions」）で同じ症状を記している（uxsim/runs/radar_loop/BRAIN_NOTES_X2.md）。"
resolution:
  perspective: [pending]
  note: "未解決。ブロック先頭の span には前ブロックの末文（同じ節のとき）を Previous Span として添える。隣接式の添付（IK-0714）と同じ条件づけ（同じ小節・続きの書き出しのときだけ）で窓を広げる案。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0714, IK-0793]
view_of: []
history: []
---

## 課題

症状: claim_qualification でブロック先頭の span（「For this purpose …」「these approaches …」「these contributions …」）には Previous Span 欄が無く、照応先のある前ブロックの末文が渡らない。
同じブロックの 2 つ目以降の span には Previous Span がある。頭脳は Source Block や hint から推測で解いた。
原因（仮説）: 前後の文脈の窓がブロックの内側だけで組まれている。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3457・3460）、uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3410・3434）。

## 発見の観点

span ごとの Previous Span の有無を、span のブロック内の位置と突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

窓を単位の内側で組むと、単位の先頭は外の照応先を受け取れない。辞書の型 information-dropped-as-unrepresentable の近傍。
