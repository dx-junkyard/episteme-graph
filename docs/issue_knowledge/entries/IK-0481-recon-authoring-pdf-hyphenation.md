---
id: IK-0481
title: "再構成の問いの入力に PDF の行末ハイフネーション（distri-\\nbution）と改行が残っていた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "教員が承認した主張から、読める本文で再構成の問いを作る"
  layers: [reconstruction_r]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: PDF の文字層から取った主張の本文は行末のハイフンと改行を含んだまま保存され、問いの入力は本文を
    そのまま写していた（input_handling）。接続軸: 本文の形（改行・ハイフネーションの有無）を問い生成の入口で
    整える取り決めが無く、生成された問い文もそのまま保存される（contract）。
generalization:
  level: general
  general_form: "抽出元の組版の痕跡（行末の分綴・改行）を残した文字列が、整えられないまま生成の入力と保存値に届く"
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [data_inspection]
  note: "砂場の出題依頼 00003 の答えキーに distri-\\nbution が残っているのを見た。"
resolution:
  perspective: [single_point_fix]
  note: >-
    行末ハイフネーション（英数字-改行-英数字）を繋ぎ、空白を 1 個に畳む正規化を 1 つ置き（claim_context.normalize_claim_text）、
    問いの入力（本文・正規化本文・出典文）と保存する問い文の両方に通す。行末でないハイフンは残す。
  landed_in:
    - backend/core/reconstruction/claim_context.py
    - backend/core/reconstruction/input_builder.py
    - backend/core/reconstruction/worker.py
    - backend/tests/test_ik0479_0482_recon_authoring_inputs.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（修正後の出題依頼を出し直していない）"
      - "行末で分綴された複合語（self-\\nconsistent）はハイフンごと繋がる"
      - "主張の行そのものの本文（学習者に見せるリビール）は直していない"
related: [IK-0479]
view_of: []
history: []
---

## 課題

再構成の問いの入力に PDF の行末ハイフネーション（distri-\nbution）と改行が残っていた

## 発見の観点

砂場の出題依頼 00003 の答えキーに分綴が残っているのを見た。

## 解決の観点

行末ハイフネーションを繋ぎ空白を畳む正規化を 1 つ置き、問いの入力と保存する問い文の両方に通す。行末でないハイフンは残す。

## 一般化

抽出元の組版の痕跡を残した文字列が、整えられないまま生成の入力と保存値に届く。
