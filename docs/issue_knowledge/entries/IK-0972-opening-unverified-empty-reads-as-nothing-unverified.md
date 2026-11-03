---
id: IK-0972
title: "議論の開幕の「このコースの教材全体で確かめていないこと」区画は、記帳された前提が無いとき「このコーパスの中では該当がありません。」とだけ言い、学生には「確かめていないことは無い」と読める — 同じ母集団の学生向け一覧は「まだ一覧に載っている前提がありません（教員が記帳すると載ります）」と記録の不在を言い分けているのに、開幕は汎用の閉世界文を使う"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/features/discuss_opening_authoring_design.md"
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "学生が議論の開幕で、この論文・コースが確かめていないことを読み、議論の入口にする"
  layers: [discuss, doubt_d, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [meaning]
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
    処理軸 wording: core/discuss/opening.py は前提が 1 つも無いとき paper_unverified_empty_note = core/doubt/scope.py SECTION_EMPTY_NOTE（ゼミ前ブリーフの区画用の
    汎用文「このコーパスの中では該当がありません。」）を返し、discuss.js が見出し「…確かめていないこと」の下に出す。接続軸 meaning: この区画の母集団は
    「教員が確定・記帳した前提」で、空は「記帳が無い」であって「確かめていないことが無い」ではない（SL1: 記録の不在について言えるのは記録の不在だけ）。
    学習者向け未検証一覧は LEARNER_OPEN_ASSUMPTIONS_EMPTY_NOTE で不在の理由（教員が記帳すると載る）を言う。構造軸 representation: 区画ごとの空の理由を
    汎用の 1 文に畳んでいる。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "「記録が無い」区画の空を汎用の「該当なし」で言うと、見出しの否定（確かめていないこと）と重なって、反対の主張（すべて確かめた）に読める"
pattern: wording-mismatch
discovery:
  perspective: [symptom_report, invariant_audit]
  note: >-
    st-07 が議論の開幕の末尾で「このコースの教材全体で確かめていないこと — このコーパスの中では該当がありません」を読み、同じコースに各章「確かめられていない点」の
    トピックがあるのに矛盾すると受け取った。同じ学生が直前に開いた未検証の前提一覧は「教員が記帳すると載ります」と言い分けていた。
    run: uxsim/runs/c-r25-doubt-r4/20261024T233257Z-31520（seq11・14）。
resolution:
  perspective: [single_point_fix]
  note: >-
    discuss 開幕の paper_unverified_empty_note を区画専用の OPENING_UNVERIFIED_EMPTY_NOTE（「この区画には、教員が記帳した前提がまだありません（教員が記帳すると載ります）。」）にした。学習者向け未検証一覧の文と語を揃え、ゼミ前ブリーフの汎用 SECTION_EMPTY_NOTE（教員向け）は変えていない。
  landed_in:
    - "backend/core/doubt/scope.py"
    - "backend/core/discuss/opening.py"
    - "backend/tests/test_doubt_round25_wave4.py"
  verification:
    methods: [guardrail]
    unverified:
      - "開幕画面の表示と、見出しと合わせた読み上げは未確認"
related: [IK-0853, IK-0935]
view_of: []
history: []
---

## 課題

症状: 開幕の「確かめていないこと」の区画が空のとき「該当がありません」とだけ出て、学生は「確かめていないことは無い」と読む。

原因: 教員向けの区画で使う汎用の閉世界文を、意味の違う学生向けの区画にそのまま使っている。

## 発見の観点

学生の読み（症状の報告）を、SL1 の閉世界語彙の原則（記録の不在だけを言う）と照らした。

## 解決の観点

第 4 波で是正（2026-10-25）。区画専用の文で記録の不在を言う。

## 一般化

否定を含む見出しの下の空の文は、二重否定で反対の主張に読める。確かめ方は、空の区画を見出しごと読み上げ、主張の向きが「記録が無い」になっていることを見ること。

第 5 周の再演: 空の文の反転は GONE（2026-10-27）。開幕の末尾区画は「主要なものに絞って表示しています」で閉じ、論文ごとの「この論文が確かめていないこと」に項目が並んだ。残り: 項目が「番号なし: $…$…」の式の頭と同じ定型 2 文（どの範囲で確かめたかが記録されていません。覆る条件はまだ定式化されていません。）の反復で、何が確かめられていないかが言葉で読めない（表示名の残りとして IK-0901 に追記）。run: uxsim/runs/c-r25-doubt-r5/20261027T071839Z-88666（seq14）。
