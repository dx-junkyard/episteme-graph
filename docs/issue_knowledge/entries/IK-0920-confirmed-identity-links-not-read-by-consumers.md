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

## 第 4 周の再演（第 25 周・面 A 第 4 周、2026-10-25）

第 4 周の再演: 一部解消（ライブラリのエントリ詳細の「結びついた教材の部品」に論文の題名と部品名が 1 行で出た。学生 st-08・st-01 の「共通部品として」に確定した共通部品が出た。ほかの論文は PDF 版が結ばれていないので出ない（事実文は正しい）。選択質問（学習チャット）と旅の [2] は場面で走らず未評価 — st-01 は地図にノードが無く旅を開けなかった。run: uxsim/runs/c-astro-r25-share-r4/20261024T232812Z-98848 seq13・uxsim/runs/c-astro-r25-share-r4/20261024T233212Z-21482 seq11・uxsim/runs/c-astro-r25-share-r4/20261024T233359Z-43631 seq8・11）

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 未評価（確定したリンクが砂場に 1 本も無い — te-02 はエントリだけを確定し、リンクの確認は runner が引けず未実行 = H35）。学生の部品の文脈は「確かめた記録が、このコーパスの中にはまだありません」の閉世界の事実文で整合。旅は区画帰属の問いからで構造を辿らなかった（IK-0988）。run: uxsim/runs/c-astro-r25-share-r5/20261027T071522Z-64789 seq7・8・13

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: 未評価（凍結版のある確定リンクが砂場に無い）。te-03 が確定したリンクのエントリは候補のまま・未凍結で、学生 st-08 の「共通部品として」は出ず「…教員が確かめた記録が、このコーパスの中にはまだありません」（未凍結は出さない規則どおり。PDF 版との結び付きは候補のまま）。Copilot も同じことを言った。run: uxsim/runs/c-astro-r25-share-r6/20261027T122930Z-7994 seq8、uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq6

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: 未評価（3 周目）。凍結版のある確定リンクは砂場にあった（エントリ 67727cb0・TeX 版の部品は確定のまま）が、学生 st-05 の ⚓ が凍結時の部品を 1 つも開けず（IK-1051）、「共通部品として」まで届かなかった。te-05 が対の PDF 版リンクを取り消したことも前提を変えた（H45）。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

未評価（5 周目）。凍結版のある確定リンクは TeX 版の部品（ce667c63 の「TOV residual loss at collocation points」）だけで、固定コース 6be229e1 の教材は PDF 版の論文（7ef9e487）しか持たず、⚓ の部品（TOV residual constraint）のリンクは第 7 周に取り消したまま戻せない（IK-1062）。TeX 版を教材に持つ 1bad7d9f は学習者の API で ⚓ が 0 個。どの学生も確定リンクの部品に届かない前提だった（harness_fixes_share H48）。学生の「共通部品」の事実文は規則どおり（uxsim/runs/c-astro-r25-share-r8/20261027T224047Z-97606 seq8）。

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

未評価（6 周目）。固定コースは確定リンクの部品の論文を含む 1bad7d9f に戻ったが、st-07 の t11 の ⚓ は 3 個とも開かず（IK-1051）、「共通部品として」まで届かなかった（harness H55）。run: uxsim/runs/c-astro-r25-share-r9/20261031T015336Z-38765（seq7〜9・13）。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

未評価（7 周目）。(d) の st-05 は材料に topic_id が無く t11 のまま（harness H60）で、⚓「EOS network map」は文脈・共通部品・説明とも「再解析で置き換わり、後継が決まっていません。」の 1 行。旅は本人の別の問いの 1 行だけ。run: uxsim/runs/c-astro-r25-share-r10/20261031T054227Z-72202（seq7〜9・14）。

## 第 11 周の再演（第 25 周・面 A 第 11 周、2026-10-31・judge_share_r11）

未評価（8 周目）。st-05 が t1 の ⚓「磁場形態遷移診断」を初めて開けた（harness H60 の是正）が、この部品に確定した同一性リンクは無く、閉世界の事実文とタブ［この論文では］だけ（正しい）。固定コース 1bad7d9f で確定リンクを持つ部品（TOV residual loss・comp_003）が置かれているのは t10・t11 だけで、t11 の ⚓ は後継の決まらない旧い名前（IK-1051）。この経路は場面の材料ではなく砂場の状態で塞がれている（harness H69）（uxsim/runs/c-astro-r25-share-r11/20261031T102623Z-4737 seq7〜9・砂場 DB の topics）。
