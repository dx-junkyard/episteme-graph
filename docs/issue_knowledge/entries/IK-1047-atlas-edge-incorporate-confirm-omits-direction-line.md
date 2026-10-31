---
id: IK-1047
title: "関係（辺）を次版の下書きへ反映する確認に、依存の向きの一文が出ない — サーバは incorporate-preview で direction_line を返しているが、管理画面の確認モーダルが描かない"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
feature_context:
  realizing: "教員が他人の採用した依存の関係を、向きを確かめてから次版の下書きへ反映する"
  layers: [field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    接続軸 information: POST …/edge-candidates/incorporate-preview の応答は direction_line（「「暗黒物質」が「暗黒成分の物理」に依存する関係として
    記載されます。」）を持つが、admin.js openEdgeIncorporateConfirm は つなぐ項目・関係の種類・採用・根拠・メモ・時点 だけを並べ、direction_line を
    読まない。カード側（_edgeCandidateCardHtml）は描くので、同じ値が確認の段だけ落ちる。処理・構造・統制軸 none。
generalization:
  level: repo_pattern
  general_form: "サーバが確認の段のために返している事実文を、確認の画面だけが読まず、直前の一覧で見えた情報が取り消せない操作の直前に消える"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: "te-02（run uxsim/runs/c-astro-r25-atlas-r7/20261027T171656Z-52459 seq 6）と te-05（run uxsim/runs/c-astro-r25-atlas-r7/20261027T171933Z-54352 seq 7）の頭脳がともに「確認には種類『依存』だけで向きが無い」と記した。HTTP の生応答に direction_line があることと、確認モーダルの組み立てを突き合わせた。"
resolution:
  perspective: [carry_through]
  note: >-
    admin.js openEdgeIncorporateConfirm が「関係の種類」の行の直後に preview.direction_line（無ければ候補の direction_line）を並べる。
    サーバは IK-0886 の是正で既に返していた。
  landed_in:
    - "frontend/public/js/admin.js"
    - "docs/features/atlas_relation_edges_design.md"
    - "backend/tests/test_atlas_review_round25_r7.py"
  verification:
    methods: [guardrail]
    unverified:
      - "ブラウザで反映の確認モーダルに向きの一文が出ること（次周の再演）"
related: [IK-0886, IK-0939]
view_of: []
history: []
---

## 課題

症状: 関係のカードには「「暗黒物質」が「暗黒成分の物理」に依存する関係として記載されます。」が出るが、［次版の下書きへ反映…］の確認には
「関係の種類: 依存」しか出ない。依存は向きのある関係なので、反映の直前に向きを確かめられない。

原因: サーバの応答の direction_line を確認モーダルが読まない（IK-0886 の是正はサーバ側とカードまでで、確認の段に配線されていない）。

## 発見の観点

2 人の教員の通し受講（reproduction）で同じ指摘が出て、生応答とモーダルの組み立てを突き合わせた（trace_walk）。

## 解決の観点

未解決。

## 一般化

型 available-but-unwired。確かめ方: 依存の関係の反映の確認に、カードと同じ向きの一文が出ること。

## 第 7 波の是正（2026-10-28）

admin.js openEdgeIncorporateConfirm が「関係の種類」の行の直後に preview.direction_line（無ければ候補の direction_line）を並べる。 サーバは IK-0886 の是正で既に返していた。

## 第 8 周の再演（2026-10-28・judge_atlas_r8）

**GONE（無向で確認）**。反映の確認（run uxsim/runs/c-astro-r25-atlas-r8/20261027T223932Z-96896 seq 7）で「関係の種類: 関連」の直後に「「暗黒エネルギー」と「暗黒成分の物理」の関係として記載されます（向きはありません）。」が出た（サーバの `direction_line` と一致）。有向（依存）の向きの一文は今周の採用が「関連」だったため未観測。

## 第 9 周の再演（2026-10-31・judge_atlas_r9）

**GONE（有向）**。te-04 が「依存」で採用した直後のカード（run uxsim/runs/c-astro-r25-atlas-r9/20261031T003210Z-30687 seq 4）と反映の確認（seq 7）に「「宇宙膨張」が「一般相対論」に依存する関係として記載されます。」が出た。第 8 周は「関連」だけで有向は未観測だった。同じ向きは下書きの一覧（te-03 20261031T015006Z-36651 seq 2 の「— 次版で加わる関係」）にも出る。ただし凍結の影響と学生の L2 では向きが落ちる — 別の面の課題として IK-1072 に起こした。軽: 反映後のカードも「記載されます」（未来形）のまま。

## 第 10 周の再演（2026-10-31・judge_atlas_r10）

**GONE（維持）**。te-02 の反映の確認（run uxsim/runs/c-astro-r25-atlas-r10/20261031T052922Z-65581 seq 7）に「「初期宇宙・インフレーション」が「初期宇宙の物理」に依存する関係として記載されます。」。残りの面は IK-1072 / IK-1118。

## 第 11 周の再演（2026-10-31・judge_atlas_r11）

**GONE（維持）**。反映の確認に「「分子雲」が「惑星形成」に依存する関係として記載されます。」（run uxsim/runs/c-astro-r25-atlas-r11/20261031T101202Z-98536 seq 7）。同じ画面に採用のメモ（逆向き）が並び、向きを選び直す手が無いことは IK-1149。
