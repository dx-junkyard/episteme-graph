---
id: IK-0901
title: "台帳対象の表示名を引く open_assumptions.target_label が claim・theory_components の UUID・前提しか解決せず、理論操作グラフのノード ID と式の ID は ID をそのまま返す — 教員の前提の地図・未検証合意リスト・台帳の見出しは eq_op_0529 / eq_dcf、学生の未検証の前提一覧は表示投影の遮断で全行が「（本文を特定できない要素）」になる（ゼミ前ブリーフだけが別の読める名前の解決を持つ）"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/architecture/persona_enactment_testing_design.md §22.8"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/display_projection_design.md"
feature_context:
  realizing: "教員が前提の地図で点の名前から対象を選び、学生がコースの未検証の前提を名前で読む"
  layers: [doubt_d, stakes_ledger_sl, frontend_admin_ui, frontend_learning_ui]
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
    構造軸 responsibility: 台帳対象の表示名の解決が 2 つある。core/doubt/open_assumptions.py::target_label（claim は theory_claims_live、component は theory_components_live の UUID、assumption は
    assumption_nodes）と、core/doubt/seminar_brief.py の _ReadableLabels（式の印字番号・操作の日本語名）。地図・未検証合意リスト・台帳詳細・学習者一覧は前者を通り、前者は台帳の主な対象（グラフのノード ID・式の ID）を解決できず ID を返す。接続軸 information: 解決できなかった ID が表示名の欄に載り、学習者向けでは
    core/display_projection.py が内部 ID として遮断して UNIDENTIFIED_ELEMENT_TEXT に置き換えるので、名前の情報が画面まで届かない。処理軸 none。
generalization:
  level: general
  general_form: "対象の表示名を引く関数が複数あり、主な経路が通る方が対象の ID の種類の一部しか解決できないため、解決できなかった ID が表示名の欄に載り、遮断層が代わりの文言に置き換えて名前が消える"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    学生 st-03 / st-09 の『このコースの教材で、検証の記録がない前提を見る』の応答で statement が全項目「（本文を特定できない要素）」、教員 te-04 / te-01 の前提の地図の label が target_id と同じ文字列だった。一方ゼミ前ブリーフは同じ対象を『式 (eq:mod_dcf)』と名指していた。target_label から表示投影まで辿った。run:
    uxsim/runs/c-r25-doubt-r2/20261022T215750Z-73306（seq3・9・10）・uxsim/runs/c-r25-doubt-r2/20261022T220507Z-77990（seq3）・uxsim/runs/c-r25-doubt-r2/20261022T221137Z-82302（seq9）・uxsim/runs/c-r25-doubt-r2/20261022T221543Z-84621（seq9）。
resolution:
  perspective: [canonical_source]
  note: >-
    第 2 波で是正（2026-10-23）。台帳対象の表示名の正本を core/doubt/target_labels.py（LedgerTargetLabels / ledger_target_label・document 必須）にした。式は印字番号（graph_paper_layer の equation_display_label）、ノードはゼミ前ブリーフと同じ段の日本語名 + 根拠の主張、読めなければ「名前を引けない対象」。open_assumptions.target_label は委譲（未解決時に id を返す契約は不変）。台帳 GET・前提の地図・未検証合意リスト（教員・学生）・反実仮想が通る。学習者の一覧は同じ文面を畳み（merged_note）、名前を引けない行を後ろへ。
  landed_in:
    - "backend/core/doubt/target_labels.py"
    - "backend/core/doubt/open_assumptions.py"
    - "backend/core/doubt/counterfactual.py"
    - "backend/api/routes/doubt.py"
    - "backend/tests/test_doubt_ledger_document_key_round25.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "名前の解決を面ごとに直さず 1 関数に寄せ、全面がそこを通る。"
  verification:
    methods: [guardrail]
    unverified:
      - "実データの論文で全面の名前が一致することは scratch DB で未確認"
related: [IK-0899, IK-0858, IK-0593, IK-0546]
view_of: []
history: []
---

## 課題

症状: 教員の前提の地図の点・未検証合意リスト・台帳詳細の見出しが `eq_op_0529` `eq_dcf` `eq_eqcand_inline_tex_…` の内部 ID の
まま並び、何の式・どの論文かが読めない。学生の未検証の前提一覧は、表示投影が内部 ID を遮断した結果、全行が
「（本文を特定できない要素）」になり、どれが先生の記帳したものかも読めない。同じ文面の行が畳まれず長く並ぶ。

原因: 表示名の解決が 2 つに分かれていて、主経路の `target_label` がグラフのノード ID と式の ID を解決しない。
ゼミ前ブリーフの `_ReadableLabels` は式の印字番号・段の日本語名を持つが、他の面は使っていない。

## 発見の観点

学生と教員の両方で同じ対象を開き、応答の名前の欄を比べた。ブリーフだけが名前を出せていたので、解決器の分裂へ辿った。

## 解決の観点

第 2 波で是正（2026-10-23。frontmatter の resolution）。以下は起票時の是正案。是正案:

- 台帳対象の表示名の正本を 1 つにする（`core/doubt/` に `ledger_target_label(session, *, document_id, target_type, target_id)`
  を置き、`_ReadableLabels` の規則 = 式は論文層の `equation_display_label`、主グラフのノードは段の日本語名 + 理論対象、
  式の詳細のノードは操作の日本語名 + 式の表示名 を移す）。地図・合意リスト・台帳詳細・学習者一覧・ブリーフが通す。
- document_id を必須にする（IK-0899 の衝突があると ID だけでは名前が決まらない）。
- 学習者一覧は同じ文面の行を IK-0593 と同じ規約で畳む。名前が引けない行は「名前を引けない対象」と事実で書き、
  一覧の先頭に回さない。
- 段の英語名（Theory basis 等）が台帳・反実仮想に出る件は IK-0546 の残り（D層の面に日本語の段名が届いていない）。

## 一般化

同じ対象の表示名を複数の関数が解決すると、主経路だけが弱い解決のまま残る。確かめ方は、対象の ID の種類ごとに
1 件ずつ用意して全面で名前を出させ、どの面も同じ名前になることを見ること。

第 3 周の再演: 残存（2026-10-23・一部）。学習者の未検証一覧の「（本文を特定できない要素）」の羅列は消えた（名前で読める）。残るのは 2 点。①主グラフの
ノードの名前が段の英語名「Theory basis」のまま（_ReadableLabels._readable_node_label が graph の label を generic と判定せずそのまま返す。台帳詳細・
反実仮想の状態行・依存範囲の列挙が英語）②学習者の出典タブの台帳の一行は正本を通らず、式の名前を出典チャンクの formulas の label（TeX のラベル
「eq:dcf」）から取り、label の無い式は主語の無い行「この内容の検証スコープはまだ記帳されていません。」になる（app.js 3327・3337 行）。
run: uxsim/runs/c-r25-doubt-r3/20261023T033238Z-24957（seq8〜10）/ uxsim/runs/c-r25-doubt-r3/20261023T033451Z-40732（seq8）/
uxsim/runs/c-r25-doubt-r3/20261023T070217Z-13811（seq8）。論文名が見出しに無い件は IK-0934、主張の名前（agent ID）は IK-0930。

第 3 波で是正（2026-10-23）: ①主グラフのノードの名前は段の日本語名（element_vocab の正本 — 「理論の土台」）+ 根拠の主張にした（seminar_brief._ReadableLabels._readable_node_label。台帳詳細・ブリーフ・反実仮想の状態行が通る）。②学習者の台帳 GET（learner_ledger_line）に表示名の正本 target_label を足し、app.js の出典タブの行の主語はそれを優先する（式は印字番号。TeX のラベル・空の主語で行を出さない）。論文名の見出しは IK-0934、主張の名前は IK-0930 で是正。実画面は未確認（第 4 周 te-05 / st-07）。
