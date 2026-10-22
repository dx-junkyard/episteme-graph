---
id: IK-0699
title: "役割判定（rhetorical_role）が本文ブロック 1 件 = LLM 1 回で、本文ブロックの多い論文では 1 本の解析に数百回の呼び出しがかかり、上限なしの既定と LLM を肩代わりする検証が両立しにくい"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-22
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
  perspective: [order_and_budget, representation_change]
  note: "判定の要求単位を「本文ブロック 1 件」から「同じ節の連続するブロックの窓」に上げた（rhetorical_role/window.py。既定 6 ブロック・本文 6000 字まで、config > env RHETORICAL_ROLE_BLOCKS_PER_CALL / RHETORICAL_ROLE_WINDOW_MAX_CHARS > 既定、1 以下なら従来の 1 ブロック = 1 コール）。プロンプトは窓のブロックを ID 付きで並べ、ブロック ID ごとに 1 件の答えを求める。答えはブロックごとの BlockRoleAnnotation に戻し（下流の契約は不変）、span のオフセットはブロックごとにサーバ側で解決する（IK-0331 と両立）。窓の答えに欠けた・検証に落ちたブロックは窓の修復 1 回（未解決のブロックだけを問い直す）の後に従来の 1 ブロックの経路で問い直し、ブロックを落とさない。母集合・層化サンプリング・max_blocks の意味は不変。回数は summary_stats の llm_calls / windows / window_fallback_windows / window_fallback_blocks に残す。"
  landed_in:
    - "src/episteme_graph/agents/rhetorical_role/window.py"
    - "src/episteme_graph/agents/rhetorical_role/agent.py"
    - "src/episteme_graph/agents/rhetorical_role/prompt.py"
    - "src/episteme_graph/agents/rhetorical_role/repair.py"
    - "src/tests/agents/rhetorical_role/test_windowing.py"
    - "docs/pipeline/overview.md"
  verification:
    methods: [guardrail]
    unverified:
      - "実 LLM での窓判定の実行（窓にまとめたときの判定の質・修復に落ちる窓の割合・プロンプトの大きさ）は行っていない"
      - "本文ブロックの多い論文 1 本での呼び出し回数を U層の記録で読む実測"
      - "既定の窓の大きさ（ブロック数・字数）の妥当性"
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

上限を戻す（取りこぼしを再び作る）のではなく、判定の要求単位を節内の窓に上げた（order_and_budget）。出力の表現は
ブロック単位のまま、要求の表現だけを「ブロック ID 付きの束」に変えた（representation_change）。窓が答えきれなかった
ブロックは 1 ブロックの経路に戻すので、窓は取りこぼしの理由にならない。

## 一般化

取りこぼし防止のために上限を撤廃したとき、呼び出しの単位を見直さないと回数が入力規模に比例する。確かめ方は、本文ブロックの
多い論文 1 本で役割判定の呼び出し回数を U層の記録で読み、宣言した予算の範囲に収まり、かつ coverage に取りこぼしが無いことを見る。
