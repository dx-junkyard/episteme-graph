---
id: IK-1152
title: "エントリの詳細で記号の結び付きを［確認］した直後の知らせが、部品の文言「学生の「共通部品として」に出るのは、この共通部品に凍結した版があるときです」のまま — 記号の区画の説明（学生の記号の吹き出しに概念名として出る）・凍結の確認文と食い違う"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員が記号と概念の結び付きを確定したとき、それが学生のどの画面にいつ出るかを正しく知る"
  layers: [concept_registry, deliberation_w, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [meaning]
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
    処理軸 wording: core/deliberation/identity_links.py LINK_CONFIRMED_FACT は要素型を問わず 1 文で、decision_outcome_fact は instance_element_type を
    見ない。記号のリンク（IK-0983 で足した経路）でも部品の届き先（部品の文脈の「共通部品として」）を言う。接続軸 meaning: 同じ画面の区画の説明
    「確定した結び付きは、凍結した版があるとき学生の記号の吹き出しに概念名として出ます」と、知らせの届き先の語がずれる。
generalization:
  level: repo_pattern
  general_form: "要素型を足した経路が、既存型のために書いた結果の知らせを型で分けずに再利用し、届き先の説明だけが元の型のまま残る"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: >-
    第 25 周 面 A 第 11 周（judge_share_r11）。te-03 が PDF 版の L_TOV を［確認］→「結び付きを確定しました（〔確定〕 確定: 岡村 千尋・2026-10-31）。学生の
    「共通部品として」に出るのは…」（uxsim/runs/c-astro-r25-share-r11/20261031T101202Z-98535 seq 10）。頭脳「記号の確定なのに部品の言い方」。
resolution:
  perspective: [vocabulary_table, single_point_fix]
  note: >-
    記号用の知らせ label_vocab.SYMBOL_LINK_CONFIRMED_FACT（「学生の記号の吹き出しに概念名として出るのは、この共通部品に凍結した版があるときです」— 凍結の確認文と同じ届き先の語）を足し、identity_links.decision_outcome_fact に instance_element_type を渡して記号のときだけそれを選ぶ。route（deliberation._decide_identity_link）は確定前の行の instance_element_type を渡す。部品の文と見送り・戻しの知らせは不変。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/core/deliberation/identity_links.py"
    - "backend/api/routes/deliberation.py"
    - "backend/tests/test_share_round25_r11_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "記号の結び付きを［確認］した直後の知らせを砂場の画面で読むこと"
related: [IK-0983, IK-1124, IK-0953]
view_of: []
history: []
---

## 課題

記号の結び付きの確定の知らせが、学生の「共通部品として」に出ると言う（記号は吹き出しの概念名に出る）。

## 発見の観点

場面の教員の確定（reproduction）。

## 解決の観点

未着手。知らせを要素型で分ける。

## 一般化

型を足した経路は、結果の知らせ・届き先の語も型で分ける。見分け方: 新しい型で操作した直後の知らせに、元の型の画面名が出るか。

## 第 11 波の是正（2026-10-31）

知らせを要素型で分けた（resolution.note）。
