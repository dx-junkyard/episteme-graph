---
id: IK-0454
title: "latex の無い inline 式候補（J=3–2・w0・αK）の [[FORMULA_N]] が「（この数式は教材に載せられていません）」に潰され、付録には eq_eqcand_inline_blk_… の内部 ID が出る"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、授業用ドラフトの本文から描けない数式プレースホルダーを片付け、参照する数式の付録を付ける
  layers: [course_builder, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [contract]
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
    処理軸: inline 式候補（equation_semantics の eqcand_inline_*）は PDF 由来で信頼しないため latex / plain_text を持たず、
    本体は source_extraction.raw_text（J=3–2 などの短い原文）だけにある。_resolvable_formula_keys は latex / plain_text しか
    見ず、raw_text だけの式を「引けない」に数えて事実文へ置き換えていた。付録（_ensure_required_equations_in_material）は
    label が空だと式 ID をそのまま見出しに出していた（logic）。接続軸: 学習画面の [[FORMULA_N]] は latex || summary で描き、
    無ければ式 ID を出す。plain_text だけの式を「描ける」と数える判定は画面の描き方と食い違っていた（contract。medium:
    どの場面で ID が出たかは第 10 周の画面で確かめていない）。
generalization:
  level: repo_pattern
  general_form: 描けるかどうかの判定が、実際に描く側の規則と別のフィールドを見ている
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction]
  note: 第 10 周の本文で自明な記号（J=3–2・w0・wa・αK・µ(a)）の位置に事実文が並んだ。
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    「描ける」は学習画面と同じ latex / summary に揃えた。latex の無い式は plain_text → raw_text の短い原文（40 字以内・
    改行なし・内部 ID でない）を本文へ直接書き（TeX らしい記号を含むときだけ $…$ で包む）、書けないものだけ事実文にする。
    付録の見出しは label → 印字番号 → 短い原文 → 内部 ID でない式 ID → 固定文の順で決め、抽出段の式 ID で本体の無い式は
    付録に出さない。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0453_thesis_support_evidence.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（第 10 周のコースで、記号の位置に原文が出て事実文が減るか・付録に内部 ID が出ないか）
      - 生成モデルが本文に直接書いた ![[equation:eq_eqcand_inline_…]] は学習画面の raw_text 描画に任せており、ここでは書き換えていない
related: [IK-0389]
view_of: []
history: []
---

## 課題

自明な記号が「載せられていない」事実文に置き換わり、付録には抽出段の式 ID が出た。

## 発見の観点

本文に並んだ事実文の位置と、その式が持っている原文を突き合わせた。

## 解決の観点

描けるかの判定を画面の描き方に揃え、短い原文はそのまま本文に書く。

## 一般化

描けるかどうかは、描く側と同じフィールドで判定する。
