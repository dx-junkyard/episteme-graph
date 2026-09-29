---
id: IK-0483
title: "再構成の問いで予測（predict）が一度も選ばれない（下地の判定が役割付きの概念 2 個以上か関係型の式を要するが、分野未指定の解析では主張の概念と式が空のまま）"
status: open
recorded_at: 2026-09-28
resolved_at: null
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
  perspective: [pending]
  note: >-
    未着手。同じ文書の別の主張（同じ種類）を誤答にする案は採らない — 誤答が論文中の正しい主張になり、
    選択肢の排他性と非LLM の照合の意味（外れ＝食い違いの可能性）を壊す。入力側で直す案: 主張の概念接地
    （登録簿の確定ラベル・DSL ノード名）を主張の概念に役割付きで届ける、または主張を指す式の対応
    （linked_claim_ids）を解析で埋める。どちらも上流の設計を切ってから直す。IK-0479 の親からの概念の引き継ぎは
    親に概念があるときだけ効く。
  landed_in: []
related: [IK-0479]
view_of: []
history: []
---

## 課題

再構成の問いで予測（predict）が一度も選ばれない（下地の判定が役割付きの概念 2 個以上か関係型の式を要するが、分野未指定の解析では主張の概念と式が空のまま）

## 発見の観点

砂場の出題依頼 10 件がすべて restate を下地にしていること、承認済み主張の種類・概念・式の列を読み取り専用で数えた。

## 解決の観点

未着手。同じ文書の別の主張を誤答にする案は採らない（誤答が論文中の正しい主張になり、選択肢の排他性と照合の意味を壊す）。主張の概念接地を役割付きで届けるか、主張を指す式の対応を解析で埋める案を、上流の設計を切ってから検討する。

## 一般化

選択肢型の出題の装置は揃っているが、その可否を決める構造化された入力を上流が作らず、装置に一度も届かない。
