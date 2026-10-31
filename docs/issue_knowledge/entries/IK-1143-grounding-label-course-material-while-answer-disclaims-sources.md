---
id: IK-1143
title: "AI が本文で出典を「使っていない」と言うのに、出所の帯は「教材に基づく」で、使っていない出典が並ぶ"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/assistant_screen_adapter_design.md"
feature_context:
  realizing: "回答の出所の帯と出典の一覧が、本文が実際に拠った資料と一致する"
  layers: [rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [completion]
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
    content_grounding は生成前に「文脈に置いて問いに関わった資料」（採用した出典・表示中の教材の関わり）で決まり、生成後は本文が 1 つも引かず教材も関わらないときだけ model_generated に直していた。表示中の教材が関わると判定された往復では、本文が出典を否定しても course_material のまま、引用していない出典が cited:false で並んだ。
generalization:
  level: repo_pattern
  general_form: "表示の確定を生成前の採用（代理）で決め、本文の実態で確かめない"
pattern: completion-defined-by-proxy
discovery:
  perspective: [reproduction]
  note: >-
    judge_doubt_r10.md（st-08 の地図の問い）: 「使っていない」と言う Cep B の出典 3 件と「〔出所: 教材に基づく〕」が並ぶ（STILL・IK-1112 追記として記載）。
resolution:
  perspective: [canonical_source]
  note: >-
    第 25 周 第 10 波（2026-10-31）: 生成後に _grounding_from_cited_answer で出所を確定する: 本文が肯定的に引いた出典（IK-1078 のマーカー単位判定）があればその出所（教材が引かれたか教材が関わるなら course_material、別の資料だけなら other_material）、1 つも引かず本文が出典・教材を否定する文（_answer_disclaims_sources）を含めば model_generated、それ以外は従来どおり。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave10_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "否定の言い方の取りこぼし（語の表に無い言い回し）"
      - "前提の説明（LEARNING_ADVICE）の経路は対象外"
related: [IK-1078, IK-1112, IK-0494]
view_of: []
history: []
---

## 課題

AI が本文で出典を「使っていない」と言うのに、出所の帯は「教材に基づく」で、使っていない出典が並ぶ。

## 発見の観点

judge_doubt_r10.md（st-08 の地図の問い）: 「使っていない」と言う Cep B の出典 3 件と「〔出所: 教材に基づく〕」が並ぶ（STILL・IK-1112 追記として記載）。

## 解決の観点

生成後に _grounding_from_cited_answer で出所を確定する: 本文が肯定的に引いた出典（IK-1078 のマーカー単位判定）があればその出所（教材が引かれたか教材が関わるなら course_material、別の資料だけなら other_material）、1 つも引かず本文が出典・教材を否定する文（_answer_disclaims_sources）を含めば model_generated、それ以外は従来どおり。

## 一般化

表示の確定を生成前の採用（代理）で決め、本文の実態で確かめない。型 completion-defined-by-proxy。

## 第 11 周の再演（第 25 周・面 B 第 11 周、2026-10-31・judge_doubt_r11 — 固定コース 1bad7d9f・3 周連続の同じ対象）

STILL（是正の未確認範囲「否定の言い方の取りこぼし」がそのまま出た）。st-08 の 2 往復目（地図の場所の問い・selection を落とした view だけの往復）で、本文末尾が「The passages retrieved from the Cep B paper (…) are not about this map item, so I have not used them here.」と言い、本文に [出典N] が 1 つも無いのに、出所の帯は「〔出所: 教材に基づく〕」で Cep B の出典 3 件が並んだ。原因（コード確認）: _answer_disclaims_sources は文に _SOURCE_NOUN_RE（出典|教材|資料|抜粋|sources|materials|excerpts）が当たることを要るが、英語の「passages」「paper」が語の表に無い（否定の語「not used」は当たる）。もう一つ（仮説）: 1 往復目（selection.atlas_node_id あり・本文はパネルの事実だけで答えた）も出典の一覧は空で帯は「教材に基づく」— 表示中のトピック教材（DCF 法）が [現在表示中の教材] としてプロンプトに残り（製品頭脳 B 4322）、topic_material_grounds が真のままと読める（_topic_material_engages_message の判定は未確認）。重さは軽（語の表の取りこぼし）。taxonomy §8 の規律どおり unverified は書き換えず、新しい課題 IK-1150 に起票した。run: 20261031T102036Z-2368（seq9・10）・mailbox req-04322 / req-04324。
