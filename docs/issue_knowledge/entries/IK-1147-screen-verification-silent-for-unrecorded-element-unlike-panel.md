---
id: IK-1147
title: "学習者が選んでいる部品に台帳の行が無いとき、部品の説明の一行は「この内容の検証の記録はまだありません。」と言うのに、AI の画面文脈には検証の行が何も載らず、AI は「いま手元の文脈には示されていません／この文脈からは言えません」と答えて、同じ部品について画面と AI の言い方が分かれる"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/features/assistant_screen_adapter_design.md"
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "学習者が画面で見ている部品の検証の記録について、画面の一行と AI の答えが同じ事実を言う"
  layers: [screen_adapter_sa, doubt_d, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（コード + 製品頭脳の要求）: 学習者の台帳 GET（routes/doubt.py get_learner_ledger_line）は行が無いとき _learner_unrecorded_line で
    exists:false と fact_line「この内容の検証の記録はまだありません。」を返す（IK-0971）。一方、学習チャットの画面文脈（routes/learning.py の
    screen_context 解決 → learner_ledger_line）は行が無いと None を返し、sources["ledger"] を置かないので resolve_verification は何も言わない
    （IK-0971 の記録「SA層 verification 解決器は従来どおり行なしで何も言わない」）。mailbox req-04320（st-05・⚓4 磁場形態遷移診断 comp_014
    → c0e016d1）の画面文脈に検証の行が無く、回答は「検証の記録は、いま手元の文脈には示されていません」。同じ run の部品の説明の一行
    （ledger/component/comp_014 = exists:false）は「この内容の検証の記録はまだありません。」。接続軸 information = 行が無いことの事実
    （記録なし）が 2 つ目の消費者（画面文脈）へ運ばれない。閉世界の拘束（IK-1141）は載っており、回答は捏造していない — 言い方の差。
generalization:
  level: repo_pattern
  general_form: "「記録が無い」という事実を一方の出口では文にし、もう一方の出口では材料を省くので、同じ対象について「無い」と「分からない」が並ぶ"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    製品頭脳 A 4320（BRAIN_NOTES_product_A_r11.md:「台帳の検証状態の行が grounding に無く『記録なし』か『未取得』か区別できない」）から、
    画面文脈の解決（learner_ledger_line の None）と学習者 GET のフォールバック（_learner_unrecorded_line）を突き合わせた。
    run: uxsim/runs/c-r25-doubt-r11/20261031T101717Z-1116（seq7・8・10）。
resolution:
  perspective: [carry_through]
  note: >-
    第 25 周 第 11 波（2026-10-31）: 行なしの一行の公開入口 routes/doubt.py::learner_unrecorded_ledger_line（IK-0899 の論文ゲート付き）を足し、学習チャットの画面文脈（_screen_context_sources の verification）は learner_ledger_line が None のときこれを sources["ledger"] に置く。学習者 GET も同じ正本（learner_ledger_line_or_unrecorded）を通す。要素は grounding の範囲で解決済みのときだけ引く。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave11_doubt_studio_chat.py"
    - "backend/tests/test_assistant_context_learning_route.py"
  verification:
    methods: [guardrail]
    unverified:
      - "再演で AI が「記録はまだありません」と言うこと"
      - "閉世界の拘束文が行なしでも付くようになった副作用の実会話"
related: [IK-0971, IK-1075, IK-1141]
view_of: []
history: []
---

## 課題

症状: st-05 が t1 の ⚓4「磁場形態遷移診断」を開いたまま「この部品の主張は、どの条件のもとで確かめられているの？」と尋ねると、AI は「検証の記録は、いま手元の文脈には示されていません。
どの範囲で確かめられたかは、この文脈からは言えません」と答えた。同じ部品の説明の一行は「この内容の検証の記録はまだありません。」。画面は「無い」、AI は「分からない」と言う。

原因: 行の無い台帳の一行（IK-0971 の exists:false）は学習者の台帳 GET だけが作り、画面文脈の解決は行が無ければ材料を置かない。

## 発見の観点

製品頭脳 A の所見から、画面文脈の解決と学習者の GET の 2 つの出口を並べた（reproduction / trace_walk）。

## 解決の観点

第 11 波で是正（frontmatter の resolution）。以前の案: フォールバックの一行を 1 箇所の正本にし、画面文脈も呼ぶ。重さは軽〜中（回答は閉世界を守っていて捏造は無い。言い方の差）。

## 一般化

型 rule-enforced-per-surface。「記録なし」の事実を出口ごとに作ると、作らない出口で「分からない」に化ける。
