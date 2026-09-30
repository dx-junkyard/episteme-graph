---
id: IK-0483
title: "再構成の問いで予測（predict）が一度も選ばれない（下地の判定が役割付きの概念 2 個以上か関係型の式を要するが、分野未指定の解析では主張の概念と式が空のまま）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
  - docs/features/reconstruction_loop_design.md §4.2
feature_context:
  realizing: "学習者に量どうしの関係を予測させ、選択肢で構造照合する"
  layers: [reconstruction_r]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 予測の下地は「関係型の種類 かつ（概念 2 個以上 または 関係型の式）」で決まる。砂場の承認済み主張は
    関係型が 1 件だけで、概念・式は全件空（分野未指定の解析では概念の割り当ても式の対応も主張に届かない）。
    予測の問い・検証・非LLM の照合は揃っているが、判定に要る入力が上流から来ない（information）。
    構造軸 low: 概念を主張に持たせる経路（概念接地の辞書）をどこで持つかを決めていない。
generalization:
  level: general
  general_form: "選択肢型の出題の装置は揃っているが、その可否を決める構造化された入力を上流が作らず、装置に一度も届かない"
pattern: last-mile-missing
discovery:
  perspective: [data_inspection, trace_walk]
  note: "砂場の出題依頼 10 件がすべて restate を下地にしていること、承認済み主張の種類・概念・式の列を読み取り専用で数えた。"
resolution:
  perspective: [carry_through, explicit_contract]
  note: >-
    予測の下地を捏造せずに作れる入力を 2 つ調べた。①概念: 分野未指定の解析では主張の概念接地（登録簿の確定ラベル・
    DSL ノード名）は既存の概念に出所を付けるだけで、空の概念欄を埋めない。役割（subject / driver）を付ける
    決定論の根拠も無いので、概念の枝はこの変更では埋めない。②式: 主張の行は式の参照（equation.equation_ids）を
    持っていたのに補完が読まず、関係型を付ける経路も無かった（IK-0502）。主張が指す式を knowledge_equations で
    解決し、PDF からそのまま抽出できた関係型の式（confidence_policy で判定・記号 2 つ以上）にだけ関係型を付ける。
    復元した式は答えキーにしない。予測にできない主張は restate のまま、理由（claim_type_not_relational /
    fewer_than_two_concepts_and_no_equation / equation_not_source_extracted / equation_type_not_relational /
    equation_fewer_than_two_symbols / 出題者が下げた author_downgraded_to_restate）を item 生成の監査 metadata
    （restate_reason）とオーサリング報告（restate_reasons）に残す（item_builder.elicit_mode_decision）。
    同じ文書の別の主張を誤答にする案は採らない（誤答が論文中の正しい主張になり、選択肢の排他性と非LLM の照合の
    意味を壊す）。
  landed_in:
    - backend/core/reconstruction/claim_context.py
    - backend/core/reconstruction/item_builder.py
    - backend/core/reconstruction/worker.py
    - backend/tests/test_ik0502_recon_predict_inputs.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（砂場の承認済み主張が指す式が信頼の条件を満たすか・predict が実際に選ばれるかは見ていない。PDF 経路の式は復元由来が多く、restate のまま理由だけが残る可能性がある）"
      - "概念の枝（役割付きの概念 2 個以上）は分野未指定の解析では引き続き空"
related: [IK-0479, IK-0502, IK-0503]
view_of: []
history: []
---

## 課題

再構成の問いで予測（predict）が一度も選ばれない（下地の判定が役割付きの概念 2 個以上か関係型の式を要するが、分野未指定の解析では主張の概念と式が空のまま）

## 発見の観点

砂場の出題依頼 10 件がすべて restate を下地にしていること、承認済み主張の種類・概念・式の列を読み取り専用で数えた。

## 解決の観点

主張が指す式（主張の行の式の参照）を解決し、PDF からそのまま抽出できた関係型の式にだけ関係型を付けて予測の下地にする（IK-0502）。概念の枝は分野未指定では埋めない。予測にできない主張は restate のまま理由を記帳する。別の主張を誤答にする案は採らない。

## 一般化

選択肢型の出題の装置は揃っているが、その可否を決める構造化された入力を上流が作らず、装置に一度も届かない。
