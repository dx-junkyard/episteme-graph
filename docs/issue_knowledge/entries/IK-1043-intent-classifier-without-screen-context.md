---
id: IK-1043
title: "学習チャットの意図分類（4 ルート）に画面の文脈が渡らず、「この項目」「ここ」が何を指すかを分類器が知らない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22"
  - "docs/features/focus_document_design.md"
  - "docs/features/assistant_screen_adapter_design.md §11"
feature_context:
  realizing: "学生が分野の地図で開いた場所や教材で選んだ要素について「この項目は」と尋ね、内容の問いとして答えてもらう"
  layers: [rag_chat, screen_adapter_sa]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: 画面の参照（地図で開いた場所の名前・選んだ要素の名前）は回答の生成には画面文脈ブロックとして渡るが、
    その手前の意図分類（_classify_intent）には発話しか渡らない。指示語の多い問いでは分類器が内容の問いか操作の問いかを
    決める手がかりが無い。製品頭脳 A 4244 が「分類器に画面文脈が渡らず『この項目』が解決されない」と記した。
generalization:
  level: repo_pattern
  general_form: "後段には渡している文脈を、前段の振り分けには渡さず、振り分けが指示語を解決できない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: >-
    製品頭脳 A 4244 / 4246 の意図分類（地図の検証記録の意味とどこで見るかの混合の問い）。分類は DOMAIN_RAG で正しかったが、手がかりは発話だけだった。
resolution:
  perspective: [carry_through]
  note: >-
    分類のプロンプトに画面の文脈を 1 行だけ添える（_intent_screen_hint: 地図で開いた場所の名前 → 選んだ要素の名前の順・40 字・内部 ID 形は捨てる・
    本文と選択テキストは渡さない = SA1）。LLM の回数は変えない。地図の場所の解決器（view）の中身を増やすのは IK-1031 の是正者の担当。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave6_chat_rag.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場で地図の場所を開いたまま「この項目」を尋ね、分類が DOMAIN_RAG になること"
related: [IK-1031]
view_of: []
history: []
---

## 課題

症状: 分類器は「この項目はどこまで確かめられている？」の「この項目」を知らない。

## 発見の観点

製品頭脳に渡った分類のプロンプトの点検。

## 解決の観点

画面の参照から組んだ名前を 1 行だけ添えた（carry_through）。画面文脈ブロック全体を分類に渡す案は、分類のプロンプトを長くし本文を渡すことになるので採らなかった。

## 一般化

前段に文脈を渡さない型。確かめ方: 画面の文脈付きの往復で、分類のプロンプトに名前の 1 行が載り、本文が載らないことを見る。

## 第 25 周 第 7 周の観察（面 A の審判・2026-10-28）

製品頭脳 A 4260 の「この部品」の分類に画面の文脈の 1 行が無かった件は製品の再発ではない。面 doubt の st-08（uxsim/runs/c-r25-doubt-r7/20261027T172411Z-57109 seq7）は部品の ⚓ を開く前（seq9）に質問しており、ランナーは学習チャットに selection.element_* を一度も送らない（runner/actions_exec.py learning_screen_context は topic 選択の形だけ）。_intent_screen_hint は element_label が無ければ何も足さない規則どおり（ハーネス H46）。第 6 波の是正の要素選択の経路は未評価。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

形を変えて STILL。要素の手がかりは app.js が selection に element_label を入れないため、本物の画面では一度も働かない（分類のプロンプト uxsim/runs/mailbox/req-04279.json に画面の行が無く USAGE_HELP — uxsim/runs/c-astro-r25-share-r8/20261027T224858Z-2266 seq11）。NEW IK-1064 に記帳。
