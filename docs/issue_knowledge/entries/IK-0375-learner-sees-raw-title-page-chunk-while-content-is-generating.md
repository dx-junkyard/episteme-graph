---
id: IK-0375
title: "コース内容の生成が終わる前に学習者がトピックを開くと、解説の代わりに論文 PDF の先頭チャンク（雑誌名・著者・所属）がそのまま出て、準備中である告知も ⚓ も無く、学習者はコースが壊れていると読む"
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 公開直後のコースを学習者が受講してトピックを読む
  layers: [rag_chat, frontend_learning_ui, course_builder]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 教材 API は student_material が無いとチャンク経路へ縮退するが、「生成中」という条件
    （course_content_status.status）を学習者向け DTO に運ばない（condition）。処理軸: 縮退の事実を言う文が無い
    （wording）。確認: 第 5 周で学生 4 名が公開直後（生成完了の 10 分前）にトピックを開き、全員が「論文の表紙が
    そのまま出た」と記録。生成完了後の再表示は未確認。
generalization:
  level: repo_pattern
  general_form: 非同期の生成が終わる前の縮退表示に、縮退している事実と理由を添えない
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: 公開直後に受講する学生ペルソナ 4 名が同じ画面を見た。
resolution:
  perspective: [explicit_contract]
  note: >-
    教材 DTO に preparation_notice（準備中／解説は生成されていません）を足し、生成の開始時に processing を永続化して両者を区別できるようにした。app.js が本文の上に事実文として描く。
  landed_in:
    - backend/api/routes/learning.py
    - backend/core/course_data.py
    - backend/core/label_vocab.py
    - frontend/public/js/app.js
    - backend/tests/test_ik0375_topic_material_preparation_notice.py
  verification:
    methods: [reproduction_rerun, docker_e2e, guardrail]
    unverified:
      - ブラウザでの表示（browser runner 未実施）
      - 先頭チャンクがヘッダである問題（チャンクの選び方は別課題）
related: [IK-0374]
view_of: []
history: []
---

## 課題

生成前の縮退表示に告知が無い。

## 発見の観点

公開直後の受講（通し受講で初めて踏める順序）。

## 解決の観点

未解決。事実文を足す。

## 一般化

縮退の事実を言わない型。
