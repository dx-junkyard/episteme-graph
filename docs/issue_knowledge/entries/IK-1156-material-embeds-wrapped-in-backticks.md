---
id: IK-1156
title: "コースの教材本文で埋め込み（![[claim:…]] / ![[component:…]]）がバッククォートで囲まれて保存され、学習画面の教材描画はインラインコードを扱わないので ⚓ の前後に裸の「`」が残る — 学習チャットの AI も選択の引用に「`「磁場形態遷移診断」`」と真似る（1bad7d9f の t1）"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
feature_context:
  realizing: "学生が教材の ⚓ を記法の残りなしに読み、AI も教材の言葉を記法なしに引く"
  layers: [course_builder, frontend_learning_ui]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 接続軸 contract: 教材本文を書く側（生成・書き換え）が埋め込みを Markdown のインラインコードで囲み、描く側（app.js の教材描画 — preserveEmbed の後の
    mdBlocksToHtml はバッククォートを扱わない）はそれを記法として解釈しない。処理軸 input_handling: 保存・描画のどちらも埋め込みの周りの記法を正規化しない。
    砂場 DB: 1bad7d9f の topics で埋め込みがバッククォートに囲まれるのは t1 だけ。どの経路（コースビルダー・原稿スタジオの AI 書き換え・教員の手）が
    書いたかは未確認（仮説）。
generalization:
  level: repo_pattern
  general_form: "書く側が描く側の解釈しない記法で本文を包み、描く側も読み飛ばさないので、記法の残りが利用者の画面と AI の材料に流れる"
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    第 25 周 面 A 第 11 周（judge_share_r11）。st-05 の t1 の教材に「`〔⚓ …〕`」（uxsim/runs/c-astro-r25-share-r11/20261031T102623Z-4737 seq 6）・AI の回答に
    「`「磁場形態遷移診断」`」（seq 13）。教材の生応答に「`![[claim:f619b79c-…]]`」があること、DB の topics で t1 だけがそうであることを確かめた。
resolution:
  perspective: [single_point_fix, explicit_contract]
  note: >-
    原因の見立て（仮説のまま）: 生成（_COURSE_CONTENT_DRAFT_PROMPT）と AI 書き換え（lecture_studio/topics.py）のプロンプトが埋め込みの記法をバッククォートで囲んで示し、LLM が本文にも写した。描く側（app.js の教材描画）はインラインコードを扱わない。是正: ①読み時 — core.lecture.topic_student_material（受講・講義・音声・readiness・学習チャットの [現在表示中の教材] が通る 1 本）で course_content_builder.unwrap_backticked_embeds（埋め込みを囲む 1〜2 個のバッククォートだけを外す・他のバッククォートは触らない）を通す。保存物は書き換えない。②書く側 — 両プロンプトに「本文に書く埋め込みはバッククォート（`）で囲まない」を足した。
  landed_in:
    - "backend/core/course_content_builder.py"
    - "backend/core/lecture.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "backend/tests/test_share_round25_r11_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "t1 を開き ⚓ の前後に記号が残らないこと・選択質問の回答にバッククォートが出ないことを砂場で"
      - "t1 の本文をどの経路（コースビルダー・AI 書き換え・教員の手）が書いたかは未確認（仮説のまま）"
      - "原稿スタジオの編集欄は保存物をそのまま見せる（囲みは残る）"
related: []
view_of: []
history: []
---

## 課題

原因は仮説（プロンプトがバッククォートで囲んだ記法を示し、LLM が写した）。

教材本文の ⚓ の前後に裸のバッククォートが出る。AI もそれを真似て引用する。

## 発見の観点

学生の教材の表示（reproduction）と DB の本文（data_inspection）。

## 解決の観点

未着手。保存時の正規化と描画時の救済。

## 一般化

本文を書く側と描く側の記法の約束を境界で検査する。確かめ方: 埋め込みを含む本文を保存・描画し、記法の残りが無いこと。

## 第 11 波の是正（2026-10-31）

原因は仮説のまま（どの経路が t1 を書いたかは未確認）。読み時の正規化と書く側のプロンプトの規則で是正した（resolution.note）。
