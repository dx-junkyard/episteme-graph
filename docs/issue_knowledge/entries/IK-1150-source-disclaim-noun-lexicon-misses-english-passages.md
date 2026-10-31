---
id: IK-1150
title: "本文が「The passages retrieved from the … paper … are not about this map item, so I have not used them here.」と出典を否定しても、出典を指す名詞の語の表に passages / paper が無いため否定と読まれず、出所の帯は「教材に基づく」のまま使っていない出典が並ぶ（IK-1143 の是正の取りこぼし）"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/features/assistant_screen_adapter_design.md"
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "回答の出所の帯と出典の一覧が、本文が実際に拠った資料と一致する"
  layers: [rag_chat]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（コード + run）: routes/learning.py の _answer_disclaims_sources は、文に _SOURCE_NOUN_RE（出典|教材|資料|抜粋|sources?|materials?|excerpts?）と
    _CITATION_NEGATION_RE の両方が当たるときだけ真。st-08 の 2 往復目の本文の否定の文は「The passages retrieved from the Cep B paper (…) are not about this
    map item, so I have not used them here.」で、否定の語（not used）は当たるが名詞（passages / paper）が表に無く偽 → content_grounding は course_material の
    まま、_displayed_sources_for が出所を決めた出典 3 件を並べた（本文に [出典N] は無い）。処理軸 input_handling = LLM の言い回し（英語の名詞）の取りこぼし。
    構造・接続・統制は無し（判定の置き場所は 1 箇所で正しい）。
generalization:
  level: repo_pattern
  general_form: "生成文の意味を決定論の語の表で読む判定は、表に無い言い回しで黙って既定側に倒れる"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: >-
    st-08 の頭脳ノート seq10（「使っていない」と言うのに出典 3 件と「教材に基づく」）と、IK-1143 の unverified「否定の言い方の取りこぼし」を突き合わせた。
    run: uxsim/runs/c-r25-doubt-r11/20261031T102036Z-2368（seq10）。
resolution:
  perspective: [single_point_fix]
  note: >-
    第 25 周 第 11 波（2026-10-31）: _SOURCE_NOUN_RE に passages / retrieved / 「provided|retrieved|given + paper|document|context|text」と「取り出した箇所」等を、_CITATION_NEGATION_RE に do/does/did not use|cite|rely on、not about|based on、without using|citing を足した。素の paper / 論文 は内容の否定と区別できないので足していない。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave11_doubt_studio_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "表の外の言い回し（他言語・婉曲）は既定側に倒れたまま"
related: [IK-1143, IK-1078]
view_of: []
history: []
---

## 課題

症状: 出典を使っていないと本文で言った英語の回答に、出所の帯「〔出所: 教材に基づく〕」と使っていない出典 3 件が付く。

原因: 否定の判定の名詞の表が英語の passages / paper を含まない。

## 発見の観点

IK-1143 の確かめていない範囲がそのまま再演で出た（reproduction）。

## 解決の観点

第 11 波で是正（frontmatter の resolution）。以前の案: 語の表を足すか、肯定の引用ゼロ + 否定の語で決める。

## 一般化

型 wording-mismatch。決定論の語の表で生成文を読む判定は、表の外の言い回しで既定側に倒れる。
