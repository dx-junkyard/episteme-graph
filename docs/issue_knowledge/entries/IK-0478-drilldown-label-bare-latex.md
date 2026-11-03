---
id: IK-0478
title: "書き直し（英語）の往復で、「詳しく聞く」ボタンの文字に $ で囲まれていない生の LaTeX（why \\alpha_K>0 implies \\mu\\ge1）が出た"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "回答の末尾の「詳しく聞く」ボタンを学習者が読める平文にする"
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: IK-0451 の _strip_math_delimiters は $…$ / \(…\)
    の区切りを外すだけで、モデルが区切りを付けずに書いた制御綴りはそのまま残った（input_handling）。書き直しの経路も同じ extract_inline_actions
    を通っていたので、経路の違いではなく入力の形の違い。
generalization:
  level: general
  general_form: "整形の前提（数式は区切り記号で囲まれている）を満たさない入力がそのまま表示に届く"
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [symptom_report]
  note: "学習者（st-04）の感想「two buttons show raw code like \\alpha_K>0 implies \\mu\\ge1」。"
resolution:
  perspective: [single_point_fix]
  note: >-
    _strip_math_delimiters が区切りを外したあと、区切りの無い制御綴りも平文に直す（ギリシャ文字・不等号などは記号に、\rm 等の書体指定は捨て、表に無い綴りは名前だけ残す。波括弧を外し、_ / ^
    の後ろの空白を詰める）。
  landed_in:
    - backend/core/learning_support_agent.py
    - backend/tests/test_ik0444_0451_chat_turn_fixes.py
    - backend/tests/test_ik0466_0478_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演"
related: [IK-0451, IK-0477]
view_of: []
history: []
---

## 課題

書き直し（英語）の往復で、「詳しく聞く」ボタンの文字に $ で囲まれていない生の LaTeX（why \alpha_K>0 implies \mu\ge1）が出た

## 発見の観点

学習者（st-04）の感想「two buttons show raw code like \alpha_K>0 implies \mu\ge1」。

## 解決の観点

_strip_math_delimiters が区切りを外したあと、区切りの無い制御綴りも平文に直す（ギリシャ文字・不等号などは記号に、\rm 等の書体指定は捨て、表に無い綴りは名前だけ残す。波括弧を外し、_ / ^ の後ろの空白を詰める）。

## 一般化

整形の前提（数式は区切り記号で囲まれている）を満たさない入力がそのまま表示に届く。
