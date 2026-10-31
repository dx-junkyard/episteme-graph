---
id: IK-0355
title: 図領域を caption の横幅・reading order の直前ブロック・caption に最も近い埋め込み画像という手がかりで決め、描かれている中身で確かめないため、図の端が切れる・本文を巻き込む・図の部品が図として出る
status: resolved
recorded_at: 2026-09-24
resolved_at: 2026-09-24
sources:
  - docs/features/image_pipeline_knowledge_library_design.md §17
feature_context:
  realizing: PDF から図を画像として切り出し、教員と学習者に図として提示し、vision 解析の入力にする
  layers: [image_library_l, pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: 図領域の横幅を caption の横幅（+3%）、上端を reading order で直前のブロックの下端とし、
    埋め込み画像は caption の直上にある最寄りのものを無条件に図として採る、という判定そのものが
    誤り（logic）。確認手段: fujimoto_d.pdf で Figure 1.2（caption より広い図の両端が切れる）、
    Figure 1.1（order と位置がずれて頁上端へ縮退し本文を巻き込む）、Figure 2.1（ベクター図の部品
    として 4 箇所に置かれた波線の sprite が図として採用され、SMask を落として黒い帯になる）を
    実物の PNG で確認した。接続軸: 文書構造の段階が出す reading order を後段が「幾何的に直上」と
    読み替えていた（meaning。order は位置の代理でしかない）。構造軸: 図領域の受け皿（bbox・
    region_confidence）はあり、表せない情報ではない（none。ただし切れた辺を DB に持てない点は
    非スコープとして残したので medium）。統制軸: 順序・予算・レビュー手続は関係しない（none）。
generalization:
  level: general
  general_form: 対象の範囲を隣接する要素の幅・並び順・最寄りの候補から決め、実際の中身で確かめないため、範囲が欠ける・余計なものを含む・部品を全体として出す
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [symptom_report, data_inspection]
  note: >-
    オーナーが図の切り出し結果を見て三つの症状（本文の混入・両端の切断・黒い帯）を報告し、
    同じ PDF の該当頁のテキストブロック・描画・画像配置を読んで、範囲を決めている量が図そのもの
    ではなく caption と並び順と最寄りの画像だったことを突き止めた。
resolution:
  perspective: [single_point_fix, fail_closed]
  note: >-
    範囲は中身から決める: 本文・見出し・柱・他の caption を組版と幾何から障害物として判定し、
    caption の上（空なら下）の障害物に挟まれた帯に掛かるインクの連結成分を集める。成分は帯の
    外へはみ出してよく、切り抜き境界の向こうへ連続する要素を取り込む（オーナー提案の連続性検査）。
    「図→短い文字塊→図」と連続すれば文字塊を障害物から外す。埋め込み画像は図領域の 90% 以上を
    覆うときだけ採る。インク比・エッジ密度で情報量の乏しい画像は出さない（fail_closed）。
  landed_in:
    - backend/core/document_pipeline/figure_regions.py
    - backend/core/document_pipeline/figure_images.py
    - backend/tests/core/test_figure_regions.py
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - 2 段組の実論文（合成 PDF でのみ確認）
      - GROBID 経路の caption bbox を使った実行（PyMuPDF 経路の構造で確認）
      - docker 上のパイプライン実行と管理画面での表示
      - 再解析時に旧 run の行が新しい key へ引き継がれること
related: [IK-0356]
view_of: []
history: []
---

## 課題

**症状**: 学位論文 fujimoto_d.pdf の図の切り出しで、Figure 1.1 は図の上に本文が大きく入り、
Figure 1.2 は下の図の両端が切れ、Figure 2.1 は何もない黒い帯が図として出た。

**原因**: 図領域の決め方が図そのものを見ていなかった。横幅は caption の横幅で、caption より広い図は
必ず切れる。上端は reading order で直前のブロックの下端で、order と位置がずれる頁では頁上端まで
広がって本文を含む。caption の直上にある最寄りの埋め込み画像は無条件に図とされ、ベクター図の
部品として置かれた小さな sprite が図になった（さらに透過マスクを落として黒く塗りつぶした）。

## 発見の観点

症状報告（`symptom_report`）を起点に、該当頁のテキストブロック・描画・画像配置と、切り出された
PNG を突き合わせた（`data_inspection`）。

## 解決の観点

範囲を決める量を「隣にあるもの」から「描かれている中身」に移した（`single_point_fix`）。インクの
連結成分は、切り抜き境界の向こうに連続する要素があるかを検査しながら範囲を広げる。中身の無い
候補（空白・一様な塗りつぶし）は図として出さない側に倒した（`fail_closed`）。caption 幅や order を
補正する案は、補正の当て推量が別の頁で外れるため採らなかった。

## 一般化

範囲・実体を代理の手がかり（隣の幅・並び順・最寄りの候補）で決めると、手がかりと中身が食い違う
入力で必ず外れる。辞書の新しい型 `extent-decided-by-proxy-signal`。本文の段落範囲・表の範囲・
数式の範囲を決める処理でも同じ形で再発しうる。
