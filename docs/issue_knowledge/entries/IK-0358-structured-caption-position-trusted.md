---
id: IK-0358
title: 図の抽出が構造化パーサの caption の頁・位置・文字列をそのまま信じていたため、GROBID 経路で頁違い・本文段落の誤認・位置なし・崩れたラベルが起き、図が別物・欠落・誤った key になった
status: resolved
recorded_at: 2026-09-25
resolved_at: 2026-09-25
sources:
  - docs/features/image_pipeline_knowledge_library_design.md §18
feature_context:
  realizing: 構造化の caption を手がかりに PDF から図を切り出し、図番号の key で本文・主張・コースと結ぶ
  layers: [image_library_l, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: GROBID の figure 要素を PDF の行へ突合する段で、突合できなかった caption は bbox なし・頁 1、
    誤って突合した caption は別の頁の位置、本文段落も caption として後段へ渡り、後段はそれを事実として
    使った（information）。確認手段: 実環境の文書構造で Figure 5.22 の caption が p.169（実物は p.103）、
    bbox なしの caption 5 件（中身は数式や崩れた本文）、ラベルで始まらない caption 4 件、付録の caption の
    欠落を確認した。構造軸: 図の位置の正本を構造化の出力に置き、PDF の文字層を照合に使っていなかった
    （responsibility。どちらを正本にするかの責務の置き場所）。処理軸: ラベルの貪欲一致（空白の無い文字層で
    本文まで取る）も同時に直したが主因ではない（none。迷ったので medium）。統制軸: 関係しない（none）。
generalization:
  level: general
  general_form: 前段が推定した位置・対応をそのまま正本として使い、原本で確かめないため、推定が外れた箇所で別物を取り出したり取り落としたりする
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [data_inspection, trace_walk]
  note: >-
    IK-0357 の調査で、実環境に保存されていた GROBID 経路の文書構造を取り出し、同じ PDF に対して抽出を
    読み取り専用で再実行して、PyMuPDF 経路の結果との差を 1 件ずつ追った。
resolution:
  perspective: [canonical_source, fail_closed]
  note: >-
    caption の位置の正本を PDF の文字層に置く。文書全体の「図ラベルで始まる塊」を索引し、構造化の caption を
    ラベル（無ければ申告位置の重なり）で結び付けて頁・位置・key をそこから取る。結び付かないものは画像を
    作らない。構造化に無い caption は文字層から足す。
  landed_in:
    - backend/core/document_pipeline/figure_regions.py
    - backend/core/document_pipeline/figure_images.py
    - backend/tests/core/test_figure_regions.py
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - docker 上の GROBID 経路での新規取り込み
      - 和文の論文（「図 3」の caption）
      - caption がラベルを持たない書式の論文
related: [IK-0355, IK-0356, IK-0357]
view_of: []
history: []
---

## 課題

**症状**: 絵の無い行（p.1 に並ぶ Figure 5 / p1_i0 など）、本物の図が caption なしの画像になる（Figure 5.22 / 6.10）、
数式や本文段落が図として切り出される、付録の図が出ない、arXiv 論文で key が `fig_1_theflowchart...` になる。

**原因**: 図の抽出が構造化パーサの caption（頁・位置・文字列）を事実として使っていた。GROBID 経路では PDF の行との
突合が外れると頁 1・位置なし・別の頁の位置になり、本文段落も caption になる。ラベルの切り出しも崩れた空白や
空白の無い文字層で誤った。

## 発見の観点

実環境の GROBID 経路の文書構造で抽出を再実行し（`data_inspection`）、caption ごとに頁と位置を原本と照らした（`trace_walk`）。

## 解決の観点

位置の正本を PDF の文字層に移した（`canonical_source`）。結び付かない caption は図を作らない側に倒した（`fail_closed`）。
構造化の出力を補正する案（頁のずれを推定で直す）は、推定が別の頁で外れるため採らなかった。

## 一般化

前段の推定（突合・位置付け）を後段が正本として使うと、推定が外れた箇所で別物を取り出す。代替の値（頁 1・位置なし）が
「根拠あり」として後段に届いた点は辞書の `fallback-fabricates-missing-link` に当たる。
