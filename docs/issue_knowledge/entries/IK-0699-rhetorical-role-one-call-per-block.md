---
id: IK-0699
title: "役割判定（rhetorical_role）が本文ブロック 1 件 = LLM 1 回で、本文ブロックの多い論文では 1 本の解析に数百回の呼び出しがかかり、上限なしの既定と LLM を肩代わりする検証が両立しにくい"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/architecture/knowledge_reproduction_review_2026-09-19.md"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "論文の全本文ブロックに論理役割を付け、取りこぼしなく主張採否の母集合を作る"
  layers: [pipeline_a, usage_metering_u]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [none]
    governance: [budget]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（src/episteme_graph/agents/rhetorical_role/input_builder.py の既定上限なし = 再現性レビュー J-3 と、自律周回の頭脳メモで
    1 ブロックごとに 1 回応答した記録）: 判定の呼び出し単位が本文ブロックで、呼び出し回数が母集合に比例する。統制軸 budget =
    1 本の解析あたりの呼び出し回数に宣言された予算が無く、上限なしの既定（取りこぼし防止）と呼び出し回数がトレードオフに
    なっている。構造軸 decomposition = 判定の単位が段落で、節や連続する短いブロック（式の導入句・つなぎ語）をまとめて 1 回で
    判定する粒度が無い。処理軸 none = 各回の判定はほぼ妥当（頭脳メモの大半が OK）。接続軸 none = 入出力の受け渡しで失われる
    ものは無い。製品の欠陥というより「順序と予算」の族の設計上の観察。
generalization:
  level: general
  general_form: "判定の呼び出し単位が入力の最小単位に固定され、取りこぼしを防ぐための上限撤廃がそのまま呼び出し回数の増大になる"
pattern: external-budget-exceeded
discovery:
  perspective: [data_inspection, external_constraint]
  note: "論文レーダー自律周回 1 周目で LLM の応答を頭脳（エージェント）が 1 件ずつ肩代わりし、本文ブロックの多い TeX 論文 1 本で役割判定だけで交代が要るほどの往復を数えた。式の導入句・つなぎ語・残骸のような短いブロックも 1 回ずつ消費していた。"
resolution:
  perspective: [pending]
  note: "未解決。提案: 節単位（または隣接する短いブロックを束ねた窓単位）で複数ブロックを 1 回の呼び出しで判定し、出力はブロックごとの役割に戻す。束ねても母集合は落とさない（取りこぼしは coverage で報告）。あわせて IK-0696 の残骸ブロックを母集合から外せば回数は入力の質に比例しなくなる。一括判定は span オフセットのサーバ側解決（IK-0331）と両立するかを先に確かめる。"
related: [IK-0696, IK-0101, IK-0331]
view_of: []
history: []
---

## 課題

症状: 本文ブロックの多い論文では、役割判定だけで呼び出しが数百回になる。LLM を外部の肩代わり（検証用の bridge）で応答する
運転では、1 本の解析を通すのに複数の交代が要った（uxsim/runs/radar_loop/BRAIN_NOTES_A.md / BRAIN_NOTES_B.md）。
原因: 判定の単位が本文ブロック 1 件に固定され、上限なしの既定（再現性レビュー J-3）と組み合わさって回数が母集合に比例する。

## 発見の観点

実際の呼び出しを 1 件ずつ見た（data_inspection）うえで、検証用に LLM を肩代わりする運転の上限（external_constraint）に当たった。

## 解決の観点

未解決。上限を戻す（取りこぼしを再び作る）のではなく、判定の粒度を節・窓に上げて 1 回あたりの判定件数を増やす方向が筋。

## 一般化

取りこぼし防止のために上限を撤廃したとき、呼び出しの単位を見直さないと回数が入力規模に比例する。確かめ方は、本文ブロックの
多い論文 1 本で役割判定の呼び出し回数を U層の記録で読み、宣言した予算の範囲に収まり、かつ coverage に取りこぼしが無いことを見る。
