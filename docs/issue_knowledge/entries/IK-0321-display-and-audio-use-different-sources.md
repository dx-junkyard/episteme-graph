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
  primary: structure
  facets: [structure.representation, connection.meaning]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「同じ講義の内容に、表示用（トピックの教材）と読み上げ用（取り込み由来の
    チャンク）という二つのソースがあり、分割の単位も違うこと」。同期の精度を上げても
    位置の対応が定義できないため、処理の改善では解けない（設計書 §0-1 が
    「タイムスタンプ精度を上げても解決しない」と明記）。さらに、どちらのソースを使うかが
    音声キャッシュの都合で決まる時期があり、同じ教材が画面のモードによって別物として
    出ていた。
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
history: []
---

## 課題

受講画面では、上段の教材表示がトピックの教材（単一ブロック）、読み上げ音声は取り込み由来の
チャンク（複数セグメント）という**別のソース・別の単位**を使っていた。内容も粒度も違うので、
同期は全体進捗の比率による線形スクロールという近似しか取れず、文のハイライトも文字数比の
推定にとどまっていた。のちに音声キャッシュの都合で表示ソースを切り替えた時期があり、今度は
同じ教材が受講モードによって別物（整形前の原文）として出るようになった。

原因は、同じ内容に対する正本が二つあり、対応づけの定義が存在しないこと。

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
