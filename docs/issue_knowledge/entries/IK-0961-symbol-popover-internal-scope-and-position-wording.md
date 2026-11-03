---
id: IK-0961
title: "記号の吹き出しが内部事情の言い方（「この式の中だけ」「位置を特定できないため、最初の定義を表示しています」）を学生にそのまま出し、論文全体で使う記号にも「この式の中だけ」と付く"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が教材の式の記号をタップして定義を確かめる"
  layers: [learner_experience_b, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: low
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording: backend/core/symbol_lookup.py:71 の FACT_POSITION_UNKNOWN と element_vocab.py:266 の
    equation_local「この式の中だけ」は、記号台帳のスコープ判定と位置解決の内部事情を学生向けの文にしている。接続軸 meaning（確信度 low）:
    F_EOS（論文全体で構造ネットワークの入力として使う特徴ベクトル）に equation_local のスコープが付いており、記号台帳のスコープ判定の意味と学生の読みがずれる可能性がある（台帳の値は未確認）。
generalization:
  level: repo_pattern
  general_form: "内部の判定の都合を、読み手の言葉に直さずに表示文にする"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: >-
    学生 st-08 が F_EOS
    の吹き出しで「『この式の中だけ』と『位置を特定できないため…』がどういう意味か分かりにくい」と記した（uxsim/runs/c-astro-r25-share-r4/20261024T233212Z-21482
    seq9）。
resolution:
  perspective: [vocabulary_table]
  note: >-
    記号の吹き出しの文を学生の言葉にした。symbol_lookup.FACT_POSITION_UNKNOWN を「論文の中で最初に定義されている箇所を表示しています（いま見ている位置との前後は分かりません）。」に、適用範囲は学生向けの表 LEARNER_SYMBOL_SCOPE_LABELS（この式の中で定義されています / この節の中で定義されています / 論文全体で使われます）を吹き出しだけが使う（element_vocab の教員向けの語は不変）。使える範囲を断言せず「どこで定義されたか」を言う。F_EOS に equation_local が付く台帳の値そのものは確かめていない。
  landed_in:
    - "backend/core/symbol_lookup.py"
    - "docs/manual/student/02-student.md"
    - "backend/tests/test_symbol_lookup_core.py"
    - "backend/tests/test_share_round25_r4_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "記号台帳のスコープ判定（F_EOS の equation_local）の妥当性"
related: [IK-0387, IK-0893]
view_of: []
history: []
---

## 課題

症状: 記号の吹き出しの注記が学生に読めない。原因: 位置解決・スコープの内部語を表示文にしている。

## 発見の観点

再現（st-08 の (e)）。

## 解決の観点

是正済み（第 25 周・面 A 第 4 波・2026-10-25）。

旧案: 是正案: FACT_POSITION_UNKNOWN を「この論文で最初に出てくる定義を表示しています」に、equation_local を「この式で定めている記号です」に言い換える（label_vocab / element_vocab の正本 1 箇所）。F_EOS のスコープが equation_local になる理由は記号台帳で確かめる。

## 一般化

語の不一致の型。確かめ方は、位置が解けない記号と式の中だけの記号を 1 つずつタップし、注記が学生の言葉で出ることを見ること。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 消滅（「この式の中で定義されています」「論文の中で最初に定義されている箇所を表示しています（いま見ている位置との前後は分かりません）」）。run: uxsim/runs/c-astro-r25-share-r5/20261027T071307Z-52079 seq9
