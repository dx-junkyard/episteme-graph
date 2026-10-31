---
id: IK-0981
title: "同一性の候補の根拠の種類が事実と合わない — 同じ論文の別の版で式・主張の本文が一致した対を「表記の一致」と呼び（名前は違う）、その一致の事実文（どの式・主張が一致したか）は一覧にも詳細にも出ない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が同一性の候補の組が何を根拠に立ったかを読み、確認・見送りを決める"
  layers: [concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording: 規則 ②b（同じ論文の別の版の式・主張の一致・IK-0919）は mapping_justification に lexical_match を入れ（identity_candidates.py の 2b の経路で justification = JUSTIFICATION_LEXICAL のまま）、画面は lexical_match を「表記の一致」と訳す — 名前の違う対（Physical consistency result ⇄ diagnostic、Prior propagation check ⇄ Physics prior constraints）に「表記の一致」が付く。接続軸 information: 2b の事実文（SAME_PAPER_REASON_*）は候補の reason に残るが、一覧の応答（routes/library.py list_identity_candidates の items）にもエントリ詳細にも運ばれない。
generalization:
  level: repo_pattern
  general_form: "判断の根拠の種類を既存の語彙に寄せて記録し、実際の根拠と違う呼び名で利用者に見せる"
pattern: wording-mismatch
discovery:
  perspective: [reproduction, doc_code_diff]
  note: >-
    te-02 が「表記の一致」と書かれた組の名前が違うことに気づいた（uxsim/runs/c-astro-r25-share-r5/20261027T071135Z-43804 seq2）。規則 ②b の justification と画面の訳語を突き合わせた。
resolution:
  perspective: [carry_through, representation_change]
  note: >-
    版の規則（②b）の理由に一致した材料を書いた: SAME_PAPER_REASON_* に続けて「一致したもの: 」+ 式の表示ラベル（graph_paper_layer.schema.equation_display_label・「式 (12)」）と主張の冒頭 40 字の引用（上限 3 件）。リンクの evidence に {basis: same_paper_version, matched} を残し、一覧の行（routes/library.py _identity_candidate_item）に reason と basis_label「同じ論文の別の版での一致」を運ぶ。mapping_justification の語彙表には値を足さず lexical_match のまま（並行する波で migration の採番を衝突させないため・一級化は次の判断）。admin.js は basis_label があれば「表記の一致」の代わりに出し、reason を行の下に 1 行。詳細（結びついた教材の部品）の候補の行にも reason。
  landed_in:
    - "backend/core/library/identity_candidates.py"
    - "backend/api/routes/library.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_concept_registry_identity_candidates.py"
    - "backend/tests/test_concept_registry_candidates_api.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実データの式の印字番号が表示ラベルに出ること（TeX 経路の label 列）"
      - "mapping_justification の語彙表への一級化"
related: [IK-0919, IK-0980, IK-0917]
view_of: []
history: []
---

## 課題

症状: 「表記の一致」と書かれた候補の 2 つの部品の名前が違う。何が一致したのかを画面のどこからも読めない。原因: 同じ論文の別の版の規則が新しい根拠の種類を持たず lexical_match を流用し、根拠の事実文も一覧・詳細へ運ばれない。

## 発見の観点

再現（te-02 (a) seq2）とコードと画面の差分。

## 解決の観点

是正案: ① mapping_justification の語彙に「同じ論文の別の版での一致」に当たる値を足す（語彙表 + migration のシード + label_vocab。KO7 と同じ作法）か、既存語彙のまま reason を一級に運ぶ。② 一覧の各リンク行・エントリ詳細に reason（式・主張の本文が一致した事実文・相手の名前なし = KR10）を 1 行出す。LLM 0 回。

## 一般化

根拠の種類を近い既存の語に寄せる型。確かめ方は、名前の違う 2 版の対の候補で「表記の一致」が出ず、何が一致したかの 1 行が出ることを見ること。

## 第 5 波の是正（第 25 周・面 A、2026-10-27）

是正済み。版の規則（②b）の理由に一致した材料を書いた: SAME_PAPER_REASON_* に続けて「一致したもの: 」+ 式の表示ラベル（graph_paper_layer.schema.equation_display_label・「式 (12)」）と主張の冒頭 40 字の引用（上限 3 件）。リンクの evidence に {basis: same_paper_version, matched} を残し、一覧の行（routes/library.py _identity_candidate_item）に reason と basis_label「同じ論文の別の版での一致」を運ぶ。mapping_justification の語彙表には値を足さず lexical_match のまま（並行する波で migration の採番を衝突させないため・一級化は次の判断）。admin.js は basis_label があれば「表記の一致」の代わりに出し、reason を行の下に 1 行。詳細（結びついた教材の部品）の候補の行にも reason。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: 新しい候補は GONE（10 番「（同じ論文の別の版での一致）」+「一致したもの: 主張「The TOV equations enter as a mean-square…」」）。旧候補 3・4・6 番は STILL: 括弧が「（表記の一致）」のまま本文は「同じ論文の別の版…の部品と、根拠の主張の本文が一致しました。」で、何が一致したかも出ない（basis_label は evidence の basis が新規則の語のときだけ付くため）。run: uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq2
