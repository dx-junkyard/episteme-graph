---
id: IK-0896
title: "わたしの地図の旅が空のとき、出口の案内が痕跡の帰属の種類を見ずに「教材のテキストを選んで質問すると1点に絞り込まれます」と言うため、まさに教材のテキストを選んで質問した直後の痕跡（segment 帰属）にも同じ案内が出て、言われた操作を済ませた学生に何が足りないかが読めない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生がテキストを選んで質問した記録から、わたしの地図の旅で論文の構成をたどる"
  layers: [personal_network, learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [condition]
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
    接続軸 condition: backend/core/personal_graph/journey.py:768-783 は空の旅の事実文を nearby.sharpen_fact_for(start_node)（backend/core/personal_graph/nearby.py:174）で選ぶが、分岐は「議論の疑似トピックか否か」だけで、痕跡の帰属が既に選択箇所（anchor_type=segment・attribution_source=learner_selected）かどうかを見ない。同じ周の痕跡は anchor_type segment / anchor_id seg_3 で、旅の開始点として理論構成に結びつかない（トピック教材の区画は claim に対応しない）のに、案内は「テキストを選んで質問する」を勧める。処理軸 logic: 済んだ操作を次の操作として勧める。構造軸は none。区画（seg_N）から構造へ降りる経路が無いこと自体は帰属の粒度の問題で、別の判断（区画 → 区画の出典チャンク → claim の決定論の対応があれば旅の [1'] に足せる）。
generalization:
  level: repo_pattern
  general_form: "状態に応じて出すべき案内を、状態の一部だけで選び、利用者が既に済ませた操作を次の一手として勧める"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    学生 st-09 が教材の本文を選んで質問した直後にわたしの地図の旅を開くと「この記録からたどれる道は、まだ記録されていません」+「教材のテキストを選んで質問するか…1点に絞り込まれます」で、言われたとおりにしたのに道が出ないと記した。痕跡の anchor と事実文の選び方を突き合わせた。run: uxsim/runs/c-astro-r25-share-r2/20261022T220528Z-78210 seq10-11。
resolution:
  perspective: [single_point_fix]
  note: >-
    core/personal_graph/nearby.py::sharpen_fact_for を帰属の種類で分けた。議論の疑似トピックは従来どおり、帰属がテキスト選択の区画・チャンク（segment / chunk）なら FACT_SELECTED_SHARPEN（帰属カードで主張や式を選んで確定する案内だけ・「テキストを選んで質問」を勧めない）、それ以外は FACT_RANGE_SHARPEN。
  landed_in:
    - "backend/core/personal_graph/nearby.py"
    - "backend/tests/test_share_round25_r2_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "区画の出典チャンク → claim の決定論の対応を旅の範囲エントリ step [1'] に足すこと（是正案の後半）は未実装"
related: [IK-0613]
view_of: []
history: []
---

## 課題

症状: テキストを選んで質問した直後の記録で旅を開くと、道が空で「テキストを選んで質問すると絞り込まれます」と言われる。
原因: 空の旅の案内が、痕跡が既に選択箇所に帰属しているかを見ない。

## 発見の観点

学生ペルソナの通し受講（reproduction）。痕跡の anchor と事実文の選び方を辿った（trace_walk）。

## 解決の観点

第 2 波（2026-10-23）で是正。案内を帰属の種類で分け、残っている操作だけを言う。

## 一般化

済ませた操作をもう一度勧める案内の型。確かめ方は、トピック縮退・選択箇所・議論の 3 種の痕跡で空の旅を開き、それぞれ実行できる
別の操作が案内されることを見ること。
