---
id: IK-0870
title: "記号の照会の事実文が対象と出所を取り違えて言う — TeX から取り込んだ論文にも「PDF からの式の読み取りには崩れが含まれることがあります」を必ず添え、「番号の無い式の左辺に現れます」はどの式かを示さず、「別の論文にも同じ記号があります」は字面が同じだけの別の量（磁場の論文の P）を同じ概念のように並べる"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "学生が記号の照会の結果から、その記号がこの論文のどの式でどう使われているかを読む"
  layers: [concept_registry, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [condition]
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
    接続軸 condition: backend/core/symbol_lookup.py:822-825 FACT_FORMULA_FALLBACK_NOTE は固定文で、:931 が教材の取り込み形式
    （TeX / PDF）を見ずに常に添える。論文の形式は documents / 解析 run に在るが、照会に渡っていない。処理軸 wording:
    :859-863 _where_text は印字番号の無い式を「番号の無い式」としか言わず、どの式かが分からない（式の先頭や定義する記号を
    添える正本 equation_display_label を使っていない）。:89 FACT_SAME_SYMBOL_IN_OTHER_DOCUMENTS は「同じ記号」とだけ言い、
    論文ごとに意味が違う（:664-666 の設計意図）ことを言わないので、学生は同じ概念と読む。
generalization:
  level: repo_pattern
  general_form: "事実文の固定の但し書きが対象の条件（入力の形式）を見ずに付き、対象を名指す語が識別できない一般名に縮退し、字面の一致を意味の一致のように並べる"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction]
  note: >-
    学生 st-02 が「この論文が PDF から読んだものか分からない」「左辺の式とはどれか」、st-02・st-03 が「字面が同じだけの
    別分野の論文を並べられても同じ概念か分からない」と記した。run: uxsim/runs/c-astro-r25-share-r1/20261022T124951Z-39393
    seq14・seq16-17・20261022T125633Z-43511 seq14・seq17。
resolution:
  perspective: [vocabulary_table]
  note: >-
    フォールバックの但し書きを採用 run の save_pdf.source_kind（無ければ source_path の拡張子）で分け、PDF と分かるときだけ PDF の崩れに触れる。番号の無い式は応答の equations（呼び名 + 式本文）で画面が数式として名指す。別の論文の行は「同じ字形の記号があります（題名）。意味は論文ごとに異なることがあります。」にした。
  landed_in:
    - "backend/core/symbol_lookup.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_share_round25_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実機の照会での表示"
related: [IK-0869]
view_of: []
history: []
---

## 課題

症状: TeX の論文に PDF の但し書きが付く。「番号の無い式」がどれか分からない。別の論文の同じ字形の記号が同じ概念のように並ぶ。
原因: 但し書きが入力の形式を見ない。式の名指しが正本の表示名を使わない。字面の一致に意味の注記が無い。

## 発見の観点

学生ペルソナの通し受講（reproduction）。

## 解決の観点

未解決。論文の形式を照会に運び、式の名指しは正本の表示名へ委譲する。

## 一般化

固定の但し書きが条件を見ずに付く型。確かめ方は、TeX と PDF の論文で同じ照会を行い、但し書きが形式に応じて変わること、
番号の無い式が区別できる名前で出ることを見ること。
