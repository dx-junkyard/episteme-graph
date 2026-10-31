---
id: IK-0956
title: "同じ題名の論文の TeX 版と PDF 版を両方取り込んだとき、ライブラリの「結びついた教材の部品」と同一性リンクの行が論文を題名だけで書き、どちらの版の部品かを区別できない"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が共通部品に同じ論文の複数の版の部品を結び、進み具合を確かめる"
  layers: [concept_registry, deliberation_w, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
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
    構造軸 representation: 行は instance_document_title（論文の題名）だけで論文を指し、版（取り込み元の形式・ファイル名）を持たない（routes/deliberation.py の
    _identity_link_response、admin.js:3300-3311）。同じ題名の 2 版（arXiv-2605.31198v1.tar.gz と PDF）は同じ文字列になる。接続軸
    information: 教材一覧の応答には filename があるのに、リンクの行の DTO には運ばれていない。
generalization:
  level: repo_pattern
  general_form: "同じ名前を持つ別の対象を、名前だけで列挙して区別の手がかりを落とす"
pattern: same-name-different-referents
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    te-05 が TeX 版を結んだ後にエントリ詳細を開き「TeX 版か PDF
    版かはこの行では分からない」と記した（uxsim/runs/c-astro-r25-share-r4/20261024T232812Z-98848 seq13）。教材一覧の応答に同じ題名の 2 行（filename
    だけが違う）があることを確かめた。
resolution:
  perspective: [carry_through]
  note: >-
    同じ題名の論文が 2 本以上あるときだけ版の手がかりを運んだ。identity_links.describe_instance が同題名の件数を数え、2 以上なら instance_document_version_label（document_version_hint = 「TeX 版・ファイル名」/「PDF 版・ファイル名」）を返し、deliberation.js の同一性リンクの行とライブラリの「結びついた教材の部品」が題名の後に添える。学生の「ほかの論文」は同じ題名が並ぶときだけ形式（TeX 版 / PDF 版）を括弧で添える（component_context._document_format_labels・ファイル名は出さない）。
  landed_in:
    - "backend/core/deliberation/identity_links.py"
    - "backend/core/component_context.py"
    - "frontend/public/js/deliberation.js"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_share_round25_r4_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "TeX 版と PDF 版を両方結んだ実データでの両画面"
related: [IK-0916, IK-0919, IK-0407]
view_of: []
history: []
---

## 課題

症状: 2 つの版の両方を結ぶ目標の進み具合（片方だけ結べた）が、ライブラリの詳細から読めない。原因: リンクの行が論文を題名だけで表す。

## 発見の観点

再現と応答の点検。

## 解決の観点

是正済み（第 25 周・面 A 第 4 波・2026-10-25）。

旧案: 是正案: 行の DTO に版の手がかり（取り込み元の形式「TeX」「PDF」・ファイル名）を足し、同じ題名が 2 つ以上あるときだけ題名の後に括弧で添える。学生向けの「ほかの論文」の列挙にも同じ規則を当てる。

## 一般化

同名別物の型。確かめ方は、同じ題名の 2 版の部品を両方結び、行が版で区別されることを画面で見ること。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 詳細側は消滅（「結びついた教材の部品」の行に「・TeX 版・arXiv-…tar.gz」「・PDF 版・….pdf」）。**一覧（同一性の候補）側は残存** — リンク 1・2 とも同じ英語題名だけで版を区別できない（admin.js renderLibraryIdentityCandidates は document_title だけを描き、一覧の応答 items に版の手がかりが無い）。教員のコース管理の同題名 2 行も版を言わない（IK-0407）。run: uxsim/runs/c-astro-r25-share-r5/20261027T071135Z-43804 seq2・4

## 第 5 波の是正（第 25 周・面 A、2026-10-27）

一覧側を直した。同一性の候補の一覧の各リンク行（routes/library.py _identity_candidate_item）に、同じ題名の論文が 2 本以上あるときだけ document_version_label（identity_links.document_version_hint ＝「TeX 版・ファイル名」/「PDF 版・ファイル名」）を運び、admin.js renderLibraryIdentityCandidates が題名の後に添える。landed_in: backend/api/routes/library.py・frontend/public/js/admin.js・backend/tests/test_share_round25_r5_fixes.py。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: 一覧も GONE。同一性の候補の各リンクに「・TeX 版・arXiv-2605.31198v1.tar.gz」「・PDF 版・2605.31198v1.pdf」が出た。「この候補を支持する論文」は題名 1 つのままで、2 版を結んだ候補か 2 本の論文かはリンクの行まで読まないと分からない。run: uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq2

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

面のみ・追記。共有モーダルの見出し「教材「Neutron Star Equation of State via Physics Informed Neural Network」の設定です」に PDF 版 / TeX 版の別が無く、te-05 は同一性の候補で見た PDF 版を開いたか確かめられなかった（詳細の結びついた部品の行には「・PDF 版・」「・TeX 版・」が出る）。run: uxsim/runs/c-astro-r25-share-r10/20261031T053605Z-69501（seq8）。
