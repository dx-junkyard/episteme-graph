---
id: IK-0313
title: 比較の条件が違う場面に同じ類似度の閾値を流用し、機能が一度も発火しなかった
status: resolved
recorded_at: 2026-08-29
resolved_at: 2026-08-29
sources:
  - docs/features/atlas_vector_anchoring_design.md §9
  - docs/features/paper_radar_design.md §12.2
feature_context:
  realizing: 論文や候補が分野の地図のどこに着地しそうかを段階ラベルで示す
  layers: [vector_anchoring_va, paper_radar, shared_infra]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「近さの段階ラベルを 1 つの閾値表で表し、比較の条件が異なる二つの用途に
    同じ表を当てたこと」。短い日本語どうしの比較と、英語の長文と日本語ラベルの比較では
    同じ指標でも値の水準が違う。閾値を 1 つ動かすだけでは、もう一方の用途が壊れるため
    構造（表現が用途の違いを表せない）。設計書 §9 が、実測で主題の合う候補が旧閾値に
    一度も届かなかったことを記録している。
generalization:
  level: general
  general_form: 尺度の成立条件が異なる対象に同じ閾値・同じ段階表を当て、常に同じ側の判定になる
pattern: threshold-reused-across-regimes
discovery:
  perspective: [data_inspection, symptom_report]
  note: >-
    着地予測がどの候補にも出ないという状態から、実データ（骨格のアンカー群と実候補）で
    値の分布を測り、主題の合う候補と合わない候補の帯を比べた。
resolution:
  perspective: [representation_change, canonical_source]
  note: >-
    用途ごとに別の閾値表を持たせ、どちらも同じ語彙のラベルに写す形にした。両表とも
    正本は共有の語彙モジュールに置き、重複定義を検出させる。どちらか一方に合わせて
    単一の閾値を動かす案は、もう一方で雑音を拾うため採らなかった。表を分けた理由
    （比較の条件が違う）を設計書に残し、実測での見直し前提も明記した。
  landed_in:
    - backend/core/label_vocab.py
    - backend/core/atlas_vectors/query.py
    - docs/features/atlas_vector_anchoring_design.md §9
related: []
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, connection.meaning]
    to: axes=processing=[none]; structure=[representation]; connection=[meaning]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は「候補に着地予測（この概念の近くに来そう）が一度も出ない」。

原因は、近さの段階ラベルを 1 つの閾値表で表し、**比較の条件が異なる二つの用途**に同じ表を
当てていたこと。一方は短い日本語どうしの比較、他方は英語の長文と日本語ラベル中心の
プロトタイプの比較で、同じ指標でも値の水準が一段下がる。実測では主題の合う候補が旧閾値に
届かず、機能が静かに無効化されていた。

## 発見の観点

「出ない」という状態から、実データで値の分布を測った（`data_inspection`）。主題の合う候補と
合わない候補の帯が分かれていること（つまり指標自体は効いていること）が、閾値の問題だと
確定させた。

## 解決の観点

表現を「用途ごとの閾値表 + 共通の段階ラベル」に変えた（`representation_change`）。閾値と
ラベルの正本は共有の語彙モジュールに一本化し、重複定義を検出させる（`canonical_source`）。
単一の閾値を動かす案は、もう一方の用途で雑音を拾うため採らなかった。

## 一般化

閾値・スコア・段階ラベルは「同じ指標なら同じ意味」と思われがちだが、比較する対象の言語・
長さ・粒度が変われば成立条件が変わる。尺度を再利用するときは、成立条件が同じかを問う。
新パターン `threshold-reused-across-regimes` として辞書へ提案する。
