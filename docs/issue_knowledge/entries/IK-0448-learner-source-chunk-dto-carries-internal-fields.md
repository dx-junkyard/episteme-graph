---
id: IK-0448
title: "学習者向けの出典ポップアップ API（GET …/source-chunk/{chunk_id}）が、式の review_reason・bbox・切り出し画像の base64・block_id・section_id をそのまま返していた"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者に返す出典のデータが描画に要るものだけになる
  layers: [rag_chat, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造・接続軸: get_chunk_passage は chunks.formulas の要素を丸ごと返し、ルートは射影せずに素通ししていた。解析の内部情報（レビュー理由・座標・画像データ・内部
    ID）が学習者に届いた（representation / information）。
generalization:
  level: general
  general_form: 内部の行をそのまま学習者向け DTO にし、描画に使うキーへ射影しない
pattern: guardrail-does-not-cover-new-path
discovery:
  perspective: [data_inspection]
  note: 第 10 周（c-astro-verify-wave456）の transcript の往復ごとの sources と mailbox の実プロンプト（req-*.json）を並べた。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    ルートに _learner_source_passage を置き、チャンクは chunk_id / text / section / source_title / formulas、式は描画に使うキー（id /
    latex / summary / plain_text / raw_text / label / reconstructed 系 / latex_note /
    label_note）だけにする。復元由来の式には教材表示と同じ印と事実文を載せる（annotate_reconstructed_formulas）。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0444_0451_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 画面の出典ポップアップ（app.js の openSourcePopup が読むキーをコードで確かめたのみ）
related: [IK-0433]
view_of: []
history: []
---

## 課題

出典ポップアップの API が内部情報を学習者に返す。

## 発見の観点

第 10 周の応答本文を読んだ。

## 解決の観点

描画に使うキーへ射影する。

## 一般化

内部の行を学習者向け DTO にそのまま出す型。
