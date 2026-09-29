---
id: IK-0410
title: "再構成ループの次の問い（GET .../reconstruction/next）が、出題できる問いが無いとき {item: null, exhausted: true} だけを返し、なぜ無いのかを言う事実文を1つも持たない"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者がトピックで「再構成に挑戦」を開き、次の問いを受け取る（R層）
  layers: [reconstruction_r]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
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
    get_next_item の 2 つの「無い」経路（コースに出題できる教材が結び付いていない / 出題できる item が無い）が、どちらも
    exhausted: true だけを返していた（処理軸 logic）。「無い理由」を DTO に載せる場所が無く、API を直接読む利用者（ハーネス・
    将来の画面）には区別がつかない（接続軸 information。medium: 学習画面 reconstruction.js は item が無いとき固定文を出している）。
    IK-0386 の同族。確認: 第 8 周で学生が「exhausted: true だけで理由が無い」と記録。
generalization:
  level: repo_pattern
  general_form: 「無い」という結果を返すとき、何が無いのかを結果の形に載せる場所が無く、理由が黙って落ちる
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: 学生が再構成の次の問いを取りに行き、exhausted だけを受け取った。
resolution:
  perspective: [single_point_fix]
  note: >-
    exhausted: true を返す 2 経路に facts を 1 つずつ持たせた（FACT_NO_SOURCE_MATERIAL「このコースには、再構成の問いを出せる教材が
    まだ結び付いていません。」/ FACT_NO_DELIVERABLE_ITEM「このトピックの再構成の問いは、いまは出題できるものがありません。」。
    数値・督促なし = P7）。「すべて答え終えた」と「まだ用意されていない」は区別していない（区別には追加のクエリが要る）。
    同じ第 8 周で報告された「確認問題で『違っていた』を押してもトピックが完了になる」（W6-22）は R層ではなく確認問題の
    自己確認（core/check_review.py の SELF_CHECK_ADVANCING・routes/learning.py）の挙動で、R層の再構成の自己確認は
    トピック完了に使われていない。前の担当は是正 F1 どおりの設計と判断していたが、この記録の時点で別担当が
    check_review.py 側を変更中なので、この課題の範囲には含めない。
  landed_in:
    - backend/core/reconstruction/schema.py
    - backend/api/routes/reconstruction.py
    - backend/tests/test_wave6b_mirror_lecture_recon.py
  verification:
    methods: [guardrail]
    unverified:
      - 学習画面 reconstruction.js は item が無いとき固定文を描き、サーバの facts を読まない（フロントは別担当）
      - 砂場での再演
related: [IK-0386]
view_of: []
history: []
---

## 課題

再構成の次の問いが「無い」とだけ返し、理由を言わない。

## 発見の観点

学生の再演で exhausted だけを受け取った。

## 解決の観点

「無い」全経路に事実文を 1 つ持たせる。

## 一般化

結果の形に「理由」の居場所が無いと、否定の結果は理由を落とす。
