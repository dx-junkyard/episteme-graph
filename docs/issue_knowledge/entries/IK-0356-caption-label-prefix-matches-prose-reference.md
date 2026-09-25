---
id: IK-0356
title: 図表 caption を「Figure N で始まる」だけで判定していたため、本文中の図参照（Figure 1.2 shows ...）が caption になり、図が二重に切り出され、付録ラベル（Figure E.1）は caption にならなかった
status: resolved
recorded_at: 2026-09-24
resolved_at: 2026-09-24
sources:
  - docs/features/image_pipeline_knowledge_library_design.md §17
feature_context:
  realizing: PDF の文書構造を復元し、図表 caption を図の切り出しと図の文脈収集の手がかりにする
  layers: [pipeline_a, image_library_l]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: caption の判定がラベルの前方一致（`^(Figure|Fig.)\s*\d+`）だけで、ラベルの後ろに
    本文の語が続くかを見ていなかった。同時に、数字しか受けないため付録の英字付きラベルを取りこぼした
    （logic）。確認手段: fujimoto_d.pdf で本文段落 21 件が figure_caption に分類され、それぞれが
    2 枚目の図として切り出されていたこと、Figure A.1 / C.1〜C.8 / E.1〜E.2 が caption にならず
    caption の無い画像として残ったこと。構造軸: 分類語彙と受け皿は足りている（none）。接続軸:
    後段は分類結果を正しく使っていた（none。抽出側にも同じ判定を置いたので medium）。統制軸:
    関係しない（none）。
generalization:
  level: general
  general_form: ラベルで始まるかどうかだけで種別を判定し、ラベルの後ろの文脈を見ないため、同じラベルを含む地の文を誤って種別に入れる
pattern: substring-match-false-positive
discovery:
  perspective: [data_inspection, symptom_report]
  note: >-
    図の切り出し不具合（IK-0355）を調べる過程で、同じ PDF の figure_caption ブロック一覧に
    「Figure N shows」で始まる本文段落が並んでいることを見つけた。
resolution:
  perspective: [single_point_fix, canonical_source]
  note: >-
    ラベルの直後に小文字の語が続けば本文とする判定を classifier に置き、validator も同じ関数を
    使う。付録ラベルを受ける。GROBID 経路や旧 artifact の caption にも効くよう、図の抽出側でも
    同じ関数で弾き、重なった caption は文書で多数派の表記を残す。
  landed_in:
    - src/episteme_graph/agents/document_structure/classifier.py
    - backend/core/document_pipeline/figure_images.py
    - src/tests/agents/document_structure/test_classifier.py
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - 和文の論文（図 N の後に本文が続く書き方）
      - GROBID 経路の caption
related: [IK-0355]
view_of: []
history: []
---

## 課題

**症状**: 図が二重に切り出される（本文段落が 2 枚目の図になる）。付録の図が caption の無い画像として出る。

**原因**: caption の判定がラベルの前方一致だけだった。`Figure 1.2 shows an overview ...` のような本文中の
図参照もラベルで始まるので caption になり、`Figure E.1:` のような英字付きの付録ラベルは数字で始まらない
ので caption にならなかった。

## 発見の観点

IK-0355 の調査で figure_caption の一覧を実データで読んだ（`data_inspection`）。

## 解決の観点

ラベルの後ろの文脈（小文字の語が続くか）を見る単一の判定を classifier に置き（`single_point_fix`）、
validator と図の抽出側（`figure_regions.looks_like_figure_caption`）がその関数を呼ぶ（`canonical_source`）。

## 一般化

前方一致・部分一致で種別を決めると、同じ語を含む地の文を拾う。辞書の `substring-match-false-positive`
（照合を部分一致で行い誤検出する）の、前方一致版にあたる。
