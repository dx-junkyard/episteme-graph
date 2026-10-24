---
id: IK-0920
title: "教員が確定した同一性リンクを読む消費者が無いか違う材料を読む — ライブラリのエントリ詳細は結びついた部品を出さず、学生の「共通部品として」の「他の論文でも使用」はリンクではなくエントリの source_document_ids を数え、学習チャットと旅（区画に帰属した問い）は共通部品を知らない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員の確定した共通部品が、教員・学生の両側で論文をまたぐ道として使われる"
  layers: [concept_registry, deliberation_w, learner_experience_b, personal_network, screen_adapter_sa, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [information]
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
    接続軸 information: 共通部品側の一覧 API（routes/deliberation.py:867-890 GET /shared-parts/{id}/identity-
    links・可視性ゲート済み）はあるが、admin.js のライブラリのエントリ詳細は呼ばない（grep で呼び出しなし）。学生側 core/component_context.py:585-598 は
    other_documents_count をエントリの source_document_ids（手で作ったエントリは空）から数え、確定リンクを読まない — app.js:8590 は件数が 0
    なら行ごと消す。学習チャットの解決器（core/assistant_context/resolvers/learning.py）に shared_part
    の読みが無い。旅（core/personal_graph/journey.py:189-290）は component / claim の帰属からだけ [2] を辿り、区画に帰属した問いからは辿らない。構造軸 responsibility:
    「リンクから論文をまたぐ」責務が書き手（W層）にだけあり、読み手の各層に配られていない。
generalization:
  level: repo_pattern
  general_form: "人が確定した対応づけを書く装置はあるが、それを読んで道を作るべき各画面がその記録を読まず、別の材料を読むか何も読まない"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-02 が確定直後にエントリ詳細を開き結びついた部品が無いと記した（uxsim/runs/c-astro-r25-share-r3/20261023T032742Z-99528 seq13）。学生 st-04
    は「共通部品として」に他の論文が無く、選択質問の回答が共通部品を知らず、旅が空だったと記した（uxsim/runs/c-astro-r25-share-r3/20261023T033353Z-33782
    seq8・seq10・seq11）。各消費者の読み元を辿った。
resolution:
  perspective: [carry_through]
  note: >-
    確定リンクの読み手を 4 面に配線した。① admin.js のライブラリのエントリ詳細に「結びついた教材の部品」区画（既存 GET /shared-parts/{id}/identity-links を読み、論文の題名・部品名・状態、閲覧できない論文は事実文。アンカー knowledge-library.bound-parts）。② core/component_context.py の _build_shared_part が確定リンクから他の論文を導き、学生が読める論文（コースの sources ∪ list_visible_document_ids。routes/learning.py が渡す）の題名を列挙（other_documents_count を廃止）。③ 学習チャットの element 解決器が確定リンクの事実 1 行と他の論文の題名を添える（新しい kind なし）。④ 旅は区画の本文に埋め込まれた部品がちょうど 1 つで一意に解決できるときだけ [2] の共通部品へ渡す（personal_graph/journey.py・queries.py）。
  landed_in:
    - "backend/core/component_context.py"
    - "backend/core/assistant_context/resolvers/learning.py"
    - "backend/core/personal_graph/journey.py"
    - "backend/core/personal_graph/queries.py"
    - "backend/api/routes/learning.py"
    - "frontend/public/js/admin.js"
    - "frontend/public/js/app.js"
    - "backend/tests/test_share_round25_identity_consumers.py"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "他の論文の題名を出すとき、読める論文の集合を呼び出し側が組んで渡し（未指定はコースの sources に縮退 = fail-closed）、その積だけを列挙する形を採った。固有の差分: 読めない論文の存在そのものも学生には出さない。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB・実画面での 4 面の通し（エントリ詳細・共通部品タブ・選択質問・旅）"
      - "admin.js / app.js の構文検査（node なし）"
related: [IK-0917, IK-0863, IK-0896]
view_of: []
history: []
---

## 課題

症状: 確定したリンクが、ライブラリのエントリ詳細にも、学生の「他の論文でも使用」にも、学習チャットにも、旅にも現れない。原因: 書く装置だけがあり、読み手の各層がその記録を読まないか、別の材料（エントリの source_document_ids）を読む。

## 発見の観点

教員の確定から教員・学生の各画面までを同じ周で辿った（reproduction・trace_walk）。

## 解決の観点

是正済み（第 3 波）。ライブラリのエントリ詳細・学生の部品の文脈・学習チャットの画面文脈・旅の 4 面が確定リンクを読む。学生には件数ではなく読める論文の題名を出す。

## 一般化

書いたが読まれない型（available-but-unwired）。確かめ方は、1 本のリンクを確定した後に、エントリ詳細・学生の共通部品タブ・選択質問・旅の 4 箇所を辿り、どこにも同じ確定の事実が出ることを砂場で見ること。
