---
id: IK-0503
title: "再構成の入力補完と記号葉プローブが、式の defined_symbols（{\"symbol\": ...} の dict）を str() で記号名として扱い、問い文や LLM 入力に dict の文字列表現が入り得た"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/features/reconstruction_loop_design.md §3.4
feature_context:
  realizing: "式の記号を使って、再構成の問いや「この記号は何を指す？」のプローブを作る"
  layers: [reconstruction_r]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [contract]
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
    接続軸: knowledge_equations.defined_symbols は equation_semantics の DefinedSymbol（symbol /
    definition_status / meaning …）の dict の列で保存されるが、再構成層は文字列の列を前提に str() で
    扱っていた（契約の食い違い。型の検査が境界に無い）。IK-0479 で knowledge_equations から式を補うように
    なってこの経路が開いた。処理軸 medium: 両形を受ける取り出しが無かった入力の取り扱い。構造・統制の軸には
    原因が無い。
generalization:
  level: general
  general_form: "構造化された要素の列を文字列の列と思い込んで文字列化し、表示や生成入力に内部表現の文字列が漏れる"
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [trace_walk]
  note: "IK-0483 の調査で knowledge_equations の列の形を永続化から辿り、再構成層の記号の扱いと突き合わせた（実害の観測はしていない）。"
resolution:
  perspective: [explicit_contract, guardrail_fix]
  note: >-
    記号名の取り出しを claim_context.symbol_names に一本化し（文字列と {"symbol"}/{"name"} の dict の両形）、
    補完・LLM 入力・記号葉プローブが使う。
  landed_in:
    - backend/core/reconstruction/claim_context.py
    - backend/core/reconstruction/input_builder.py
    - backend/core/reconstruction/item_builder.py
    - backend/tests/test_ik0502_recon_predict_inputs.py
  verification:
    methods: [guardrail]
    unverified:
      - "既に保存された item の問い文に dict の文字列が入っているかは DB を見ていない"
related: [IK-0479, IK-0502]
view_of: []
history: []
---

## 課題

再構成の入力補完と記号葉プローブが、式の defined_symbols（dict の列）を str() で記号名として扱い、問い文や LLM 入力に dict の文字列表現が入り得た。

## 発見の観点

knowledge_equations の列の形を永続化から辿り、再構成層の記号の扱いと突き合わせた。

## 解決の観点

記号名の取り出しを 1 関数にし、両形を受ける。

## 一般化

構造化された要素の列を文字列の列と思い込んで文字列化し、表示や生成入力に内部表現の文字列が漏れる。
