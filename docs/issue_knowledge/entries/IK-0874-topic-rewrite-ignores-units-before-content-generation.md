---
id: IK-0874
title: "トピックの AI 書き換えはトピックに選んだ学ぶ単位を根拠に入れないため、コース内容生成の前は根拠が空になり、AI は「学ぶ単位を紐づけてから」と既に済んだ操作を求め、画面は何も変わらなくても「提案を反映しました」と出す"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/learning_units_design.md"
feature_context:
  realizing: "コースビルダーで学ぶ単位を選んで作ったコースの各トピックに、AI で授業用ドラフトの下書きを作る"
  layers: [lecture_studio, learning_units, course_builder]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [ordering]
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
    接続軸 information: backend/api/routes/lecture_studio/topics.py の rewrite_lecture_studio_course_topic（572〜583 行）が LLM に渡す
    根拠は summary / source_excerpt / linked_*_ids / evidence_links / content_blocks / coverage だけで、topic.units を読まない。これらは
    コース内容生成（freeze）で初めて埋まるので、登録直後のコースでは全部空になる（製品頭脳への要求 uxsim/runs/mailbox/req-03999.json の
    「根拠候補」が全キー空。同じコースのコースビルダー応答 res-03993.json では先頭トピックに units [U118, U122] が選ばれている）。
    統制軸 ordering（medium）: 書き換えは「コース内容生成の後」を前提にするのに、その前でも実行でき、日次の上限も消費する
    （consume_lecture_rewrite_quota は根拠の有無の前）。処理軸 wording（medium）: AI の注意点は空の根拠から「学ぶ単位を紐づけて」と
    推し、admin-lecture-studio.js は応答の中身を見ずに「提案を反映しました」を出す。構造軸 none。
generalization:
  level: repo_pattern
  general_form: "後段の生成が、前段の確定（選んだ単位）ではなく、さらに後の工程で初めて埋まる派生値だけを材料にし、その工程の前に呼ばれると空の材料で走る"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    教員ペルソナ（任せがち）が登録直後のコースで書き換えを頼み、見出しだけの下書きと「学ぶ単位を紐づけて」の注意点で行き詰まった。
    製品頭脳への要求（req-03999）の根拠候補が空であること、コースビルダー応答（res-03993）で units が選ばれていることを突き合わせた。
    run: uxsim/runs/c-astro-r25-studio-r1/20261022T125145Z-40602（seq5・15）。
resolution:
  perspective: [carry_through, order_and_budget]
  note: "書き換えの根拠に topic.units をコースの論文の live 行（course_units.load_units_for_documents）で解決した種別・名前・要約を足した（番地は載せない — lecture_studio_state.unit_grounding_for_prompt）。根拠が全部空で下書きも見出しだけなら LLM を呼ばず日次上限も消費せず skipped + 事実文（REWRITE_NO_GROUNDING_NOTICE）を返す。教員が本文を書いた下書きはその本文が材料になるので通す。応答が下書きを変えなかったら changed:false と「提案による変更はありませんでした」を返し、画面は「反映しました」と言わない。"
  landed_in:
    - "backend/api/routes/lecture_studio/topics.py"
    - "backend/core/lecture_studio_state.py"
    - "frontend/public/js/admin-lecture-studio.js"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "事前判定と告知の文をサーバの 1 箇所（lecture_studio_state）に置き、画面は返った文を描くだけにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "コース内容生成前のコースで units を根拠にした書き換えの品質は未確認（第 2 周 te-02）"
related: [IK-0682]
view_of: []
history: []
---

## 課題

症状: コースビルダーで学ぶ単位を選んで登録した直後のコースで、トピックの [AI で書き換え] を押すと、本文は見出しのまま変わらず、
注意点に「根拠候補が1つも紐づいていません。…学ぶ単位を紐づけてから再度作成するか」と出る。画面は「提案を反映しました。保存すると
確定します。」と言う。教員は紐づけ済みのはずの単位をどこで紐づけるのか分からず止まった。原因: 書き換えの根拠がコース内容生成で
埋まる派生値だけで、選んだ単位を読まない。

## 発見の観点

ペルソナの通し受講（reproduction）と製品頭脳への要求・応答の読み比べ（data_inspection）。

## 解決の観点

解決済み（2026-10-23）。選んだ単位を根拠に入れる。根拠が空なら呼ばずに次の一手を事実文で示す。

## 一般化

確定した選択を後段が読まず、さらに後の工程の派生値だけを見る型。確かめ方は、コース内容生成をしていないコースで書き換えを呼び、
単位の中身がプロンプトに載る（または LLM を呼ばずに事実文が返る）ことを見ること。
