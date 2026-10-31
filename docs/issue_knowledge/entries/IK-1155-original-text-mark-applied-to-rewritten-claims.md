---
id: IK-1155
title: "⚓ の主張・部品名に添える「（原文）」の印が、表示する文字列が翻訳されていない（英語のまま）かだけで付き、原子化で言い換えた主張（origin=atomic_rewrite）にも付く — 学生は論文の逐語の引用と読み、学習チャットの AI が「論文の文そのものではなく言い換え。原文の引用として読まないほうが安全」と打ち消す"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/display_projection_design.md"
feature_context:
  realizing: "学生が教材の ⚓ の主張が論文のどの文か（逐語か、解析が整えた言い換えか）を見分ける"
  layers: [course_builder, frontend_learning_ui, knowledge_objects]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 logic: core/course_content_builder.py は component / claim の表示名が is_untranslated_text なら label_vocab.ORIGINAL_TEXT_MARK「（原文）」を付ける。
    label_vocab の注記は「原文の引用であることを言うだけ」だが、判定は言語だけで claim の origin（span / atomic_rewrite / equation_synthesis）を見ない。
    接続軸 meaning: 「原文」が「原文の言語」と「逐語の引用」の 2 つの意味で読まれる。砂場 DB: t1 の ⚓ の主張 f619b79c は theory_claims_live.origin =
    atomic_rewrite（「The magnetic field is bow-like near the filament head.」— 論文の要旨は「curving into a bow-like shape near the filament head」）。
generalization:
  level: repo_pattern
  general_form: "出所の印を代理の手がかり（言語が翻訳されていない）で付け、本当の出所の区別（逐語か言い換えか）を見ない"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    第 25 周 面 A 第 11 周（judge_share_r11）。st-05 の t1 の ⚓ と部品の文脈の主張に「（原文）」（uxsim/runs/c-astro-r25-share-r11/20261031T102623Z-4737 seq 6・7）。
    選択質問 2 で AI が「教材で「」に入っている英文は、論文の文そのものではなく、解析で整理した主張の言い換えです」（seq 11）。頭脳「印と中身の食い違い」。
    theory_claims_live の origin を読んだ。
resolution:
  perspective: [vocabulary_table, carry_through]
  note: >-
    印を主張の出所で選ぶ: label_vocab.original_text_mark_for_origin（span / claim_object / 不明 =「（原文）」・atomic_rewrite =「（原文の言い換え）」・equation_synthesis =「（式から組み立てた文）」）。出所は core.schema.infer_claim_origin（明示の origin か、persistence と同じ規則 = 親 claim / synth）。部品の文脈の主張（component_context._build_claims）と教材の ⚓（build_topic_evidence_items）が使い、凍結は claim の evidence_link に origin を写す。旧い凍結は読み時（course_content_builder.topic_with_claim_origins — UUID の宛先だけ theory_claims_live から origin を引く・保存物は書き換えない・fail-soft）で補い、学習画面の教材と講義の 2 経路が通す。言語の判定（is_untranslated_text）は不変。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/core/schema.py"
    - "backend/core/component_context.py"
    - "backend/core/course_content_builder.py"
    - "backend/api/routes/learning.py"
    - "backend/api/routes/lecture.py"
    - "backend/tests/test_share_round25_r11_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "t1 の ⚓ と部品の文脈で印が出所どおりに分かれることを砂場の画面で"
      - "block 付きの宛先（UUID でない）の旧い凍結の主張は従来どおり「（原文）」のまま"
      - "原稿スタジオのプレビュー（lecture_studio/topics.py）は読み時の補いを通していない"
related: [IK-0571]
view_of: []
history: []
---

## 課題

言い換えた主張にも「（原文）」が付き、学生が逐語の引用と読む。AI は別の答えでそれを打ち消す。

## 発見の観点

学生の選択質問（reproduction）と DB の主張の出所（data_inspection）。

## 解決の観点

未着手。印を出所の種別で分ける。

## 一般化

型 extent-decided-by-proxy-signal: 出所の印を言語で代用する。確かめ方: 逐語と言い換えの主張を並べて印を比べる。

## 第 11 波の是正（2026-10-31）

印を出所の種別で分けた（resolution.note）。
