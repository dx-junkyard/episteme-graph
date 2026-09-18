---
id: IK-0321
title: 表示と読み上げが別のコンテンツと別の分割単位を使い、同期が近似にしかならない
status: resolved
recorded_at: 2026-07-11
resolved_at: 2026-07-14
sources:
  - docs/features/lecture_slide_sync_design.md §0-1
  - docs/features/lecture_slide_sync_design.md
feature_context:
  realizing: 教材の表示と読み上げを、同じ位置で同じ内容として進める
  layers: [tts_voice]
classification:
  axes:
    processing: [none]
    structure: [representation, aggregation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 表示も音声生成もそれぞれ正しく動いており、同期の精度を上げても解けない。

    構造: 対応づけられる単位が存在しない点が表現に、同じ内容の正本が二つある点が集約に当たる。単位を作っても表示ソースの選択が分散したままなら残るため二値を置いた。

    接続: 内容も粒度も違うソースの間で位置の対応が定義できない点が意味に当たる。

    統制: 表示ソースの選択が音声の都合に従属していた時期はあるが、順序・予算・完了の設計そのものは崩れていない。
generalization:
  level: repo_pattern
  general_form: 同じ内容に複数のソースと粒度があり、対応づけが近似でしか定義できない
pattern: duplicate-canonical-sources
discovery:
  perspective: [symptom_report, boundary_walk]
  note: >-
    表示と音声がずれるという症状から、両者のソース・単位・API を並べた表を作り、
    対応づけが原理的に定義できないことを確認した。コード自身のコメントが同じ制約を
    明言していた。
resolution:
  perspective: [representation_change, canonical_source]
  note: >-
    「スライド + スピーカーノーツ」という単一の単位を導入し、表示と読み上げを同数・
    同順のページとして導出する（決定論・保存しない）。分割の正本はサーバ側の 1 関数に
    置き、表示・音声生成・準備判定の三者が同じ関数を通る。表示ソースの選択も 1 つの
    述語に一本化し、キャッシュの都合で選ばない。
  landed_in:
    - backend/core/lecture.py
    - backend/db/040_lecture_slides.sql
    - backend/db/047_topic_lecture_audio.sql
    - docs/features/lecture_slide_sync_design.md
related: [IK-0314, IK-0322]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, connection.meaning]
    to: axes=processing=[none]; structure=[representation]; connection=[meaning]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: axes=processing=[none]; structure=[representation]; connection=[meaning]; governance=[none]
    to: axes=processing=[none]; structure=[representation, aggregation]; connection=[meaning]; governance=[none]
    reason: 軸ごとの再判定で、同じ内容の正本が二つあるという集約の要素を構造軸の第2の値として足した
---

## 課題

受講画面では、上段の教材表示がトピックの教材（単一ブロック）、読み上げ音声は取り込み由来の
チャンク（複数セグメント）という**別のソース・別の単位**を使っていた。内容も粒度も違うので、
同期は全体進捗の比率による線形スクロールという近似しか取れず、文のハイライトも文字数比の
推定にとどまっていた。のちに音声キャッシュの都合で表示ソースを切り替えた時期があり、今度は
同じ教材が受講モードによって別物（整形前の原文）として出るようになった。

原因は、同じ内容に対する正本が二つあり、対応づけの定義が存在しないこと。

4 軸で見直すと、構造軸には二つの要素がある。対応づけられる単位が無いという表現の問題と、同じ内容の正本が二つあるという集約の問題である。単位を作っても、表示ソースの選択が別の都合で決まる限り分散は残る。

## 発見の観点

ずれるという症状（`symptom_report`）から、表示と音声のソース・単位・API を並べた境界の表を
作った（`boundary_walk`）。コード自身のコメントが「厳密な位置対応が無い」と明言しており、
精度向上では解けないことがそこで確定した。

## 解決の観点

対応づけができる単一の単位（スライドとスピーカーノーツ）へ表現を変え（`representation_change`）、
分割と表示ソースの判定をサーバ側の関数 1 つに正本化した（`canonical_source`）。表示・音声生成・
準備判定の三者が同じ関数を通ることで、プレビューと配信の食い違いも構造的に消える。

## 一般化

「同じものを別の経路で作って後から合わせる」設計は、合わせる精度をいくら上げても原理的に
合わない。合わせたいなら、合わせられる単位を先に作る。
辞書の型 `duplicate-canonical-sources` に対応する。
