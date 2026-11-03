---
id: IK-1158
title: "学習チャットに渡す式の事実行（「この箇所の式: 式 (4)［AI復元］、式 (2)［AI復元］」）が [現在表示中の教材] の本文の末尾に区切りなしでつながるため、AI が「教材の末尾にこの行が付いている」と画面に無い行を指し、教材・スライドに出ない印字番号（式 (4)）で式を呼ぶ"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "学生が講義中に「いまのスライドの式は論文の式そのものか AI の復元か」を尋ね、画面に見えている式と印で答えを確かめる"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
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
    確定（コードの読み）: backend/api/routes/learning.py の _learning_chat_core（7274〜7290 行）は、表示中のトピック教材を
    "[現在表示中の教材]\n" + 本文 の区画に置き、_topic_formula_fact_line（core/formula_facts.py・見出し FORMULA_FACT_HEADING = 「この箇所の式」）の
    1 行を同じ区画の末尾に "\n" だけでつなげる。区画の中で本文と事実行を分ける見出しも「画面には出ていない」の断りも無く、system の
    RECONSTRUCTED_FORMULA_INSTRUCTION も事実行が画面の文ではないことを言わない。事実行の式の名前は equation_printed_label（印字番号「式 (4)」）を
    優先するが、学習画面（app.js）の教材・講義の式には印字番号を描かない（記号の吹き出しにだけ出る）。接続軸 information（画面に無い派生の行が
    画面の文と同じ区画で渡る）、処理軸 wording（AI がそれを「教材の末尾の印」と言い表す）。
generalization:
  level: repo_pattern
  general_form: "AI に渡す文脈で、画面の文とシステムが添えた派生の事実を同じ区画につなげ、AI が派生の事実を画面に見えているものとして利用者に指し示す"
pattern: projection-mistaken-for-source
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 25 周 面 D 第 11 周。st-08 が講義を止めて「いまのスライドの TOV 方程式は論文の式そのものか」と尋ね、回答の「教材の末尾に『この箇所の式: 式 (4)［AI復元］、
    式 (2)［AI復元］』と印が付いている」を読んで「教材の最後にそんな行は見当たらなかった」と C 所見。製品頭脳 A（4332）も「どの式 (2)/(4) がどの表示式かの
    対応が文脈に無い」と記した。プロンプトの組み立てを辿った。run: uxsim/runs/c-astro-r25-studio-r11/20261031T102933Z-6346 seq6・14、
    uxsim/runs/round25/BRAIN_NOTES_product_A_r11.md 4332。
resolution:
  perspective: [single_point_fix]
  note: >-
    第 25 周 第 11 波（2026-10-31）: 式の事実行を [現在表示中の教材] の区画から出し、別区画 [この箇所の式（画面には出ない補助情報）]（core/formula_facts.py の OFFSCREEN_FORMULA_FACT_BLOCK_HEADING / offscreen_formula_fact_block）に置いた。区画を置いた往復だけ system に OFFSCREEN_FORMULA_FACT_INSTRUCTION（画面に無い・教材に出ていない印字番号で呼ばず式の形で指す）を足す。LLM 回数は不変。
  landed_in:
    - "backend/core/formula_facts.py"
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave11_doubt_studio_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "再演で回答が画面に無い行・番号を指さないこと"
      - "チャンク出典側の事実行（出典の区画内）は従来どおり"
related: [IK-0577, IK-1009, IK-0997, IK-0681, IK-1117]
view_of: []
history: []
---

## 課題

症状: チャットが画面に無い「教材の末尾の行」を指し、画面に出ない印字番号で式を呼ぶ。答えの中身（復元式であること）は正しいが、学生は画面で確かめられない。

原因: 式の事実行が表示中の教材の本文の末尾に区切りなしでつながって AI に渡り、画面の文と区別できない。

## 発見の観点

学生ペルソナの通し受講の症状（reproduction）から、チャットのプロンプトの組み立てを辿った（trace_walk）。

## 解決の観点

第 11 波で是正（frontmatter の resolution）。以前の案: 画面の文と派生の事実を別の区画に置き、派生の事実が画面に無いことを AI に伝える。

## 一般化

AI に渡す文脈は「画面に見えているもの」と「システムが添えた事実」を区画で分ける。確かめ方: 印の付いた式のあるトピックで式の出所を尋ね、
回答が画面に無い行・番号を指さないかを見る。
