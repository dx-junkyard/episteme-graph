---
id: IK-1116
title: "学習チャットの文脈の出典行（[出典N] 『題名』）に節の見出しが無く、抜粋の頭に見出しが入る出典だけ節を答えられ、ほかは「節不明」になる"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "学習者が「いまの説明の根拠は論文のどの節か」と尋ねたとき、出典ごとに節を答える"
  layers: [rag_chat]
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
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    search_chunks_with_metadata / get_chunks_for_prompt は chunks.source_metadata.section_title を section_title として SELECT
    しているが、routes/learning.py の出典行の組み立て（本体 RAG・持ち越し・表示中の教材の出典・前提の説明・学習相談）は題名だけを書いていた。接続軸 information（取った値が文脈まで届かない）。
generalization:
  level: repo_pattern
  general_form: "取得済みの属性（節の見出し）がプロンプトの組み立てで使われず、AI が答えられる問いに答えられない"
pattern: available-but-unwired
discovery:
  perspective: [reproduction]
  note: >-
    製品頭脳 A 4299: 根拠の節を問う 3 往復目で [出典1] は節不明と答えた（st-08・uxsim/runs/c-astro-r25-studio-r9 seq 16）。
resolution:
  perspective: [carry_through]
  note: >-
    _source_section_note が section_title を「・節「…」」にして出典行の題名の後ろに添える（制御文字除去・80 字超と内部 ID
    の形は載せない・無ければ空）。予想の前に本文を伏せる行（elicit）には添えない。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave9_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "section_title が空の教材（GROBID の見出し解決が無い PDF 経路）では従来どおり節は言えない"
      - "4299 の再演で出典ごとに節を答えること（第 10 周）"
related: []
view_of: []
history: []
---

## 課題

出典行に節が無い。

## 発見の観点

製品頭脳 A 4299: 根拠の節を問う 3 往復目で [出典1] は節不明と答えた（st-08・uxsim/runs/c-astro-r25-studio-r9 seq 16）。

## 解決の観点

_source_section_note が section_title を「・節「…」」にして出典行の題名の後ろに添える（制御文字除去・80 字超と内部 ID の形は載せない・無ければ空）。予想の前に本文を伏せる行（elicit）には添えない。

## 一般化

取得済みの属性（節の見出し）がプロンプトの組み立てで使われず、AI が答えられる問いに答えられない。型 available-but-unwired。
