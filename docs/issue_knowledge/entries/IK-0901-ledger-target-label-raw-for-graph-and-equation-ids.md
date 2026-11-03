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

第 4 周の再演: 残存（2026-10-25・一部）。台帳の見出しは「論文 › 対象」になった（IK-0934 は GONE）。残るのは、主グラフのノードの対象名が「段の日本語名: 式から合成した英語の主張（生の TeX）」
（「理論の土台: An equation of this paper defines $\mathcal{L}$.」）で、原稿スタジオのノード名は段の英語名（Theory basis / Consistency relation）のまま、支える主張の一覧に
同じ文面が 2 行並んで見分けられない（「defines $M$」）こと。支える主張には単位を記号として扱った合成文（「$K_0$ depends on MeV」— IK-0637 と同型）も出る。
run: uxsim/runs/c-r25-doubt-r4/20261024T232812Z-98847（seq5・6）/ uxsim/runs/c-r25-doubt-r4/20261024T233017Z-7034（seq5・6）。

第 4 波の判定（2026-10-25）: 「台帳の詳細: Theory basis」の英語見出しはハーネスの投影（ノードの生ラベル）で、原稿スタジオの実画面は lsGraphStageLabelJa → element-vocab の正本で「理論の土台」と出す（投影の偽所見 — sync_keys_wave4.md 面 B に記した）。残るのは、対象名・支える主張が式から合成した英語の主張と生の TeX（IK-0571 と同根）で、同じ文面の主張が 2 行並ぶこと・単位を記号に扱う合成文（IK-0637 と同型）。いずれも A層の合成文の側の課題で、この波では直していない。

第 5 周の再演: 残存（学習者の面・2026-10-27）。学生の未検証の前提一覧と開幕の「この論文が確かめていないこと」の項目が「番号なし: $\frac{1}{2} \ddot{\mathrm{I}} = 2$…」のような途中で切れた式の頭で、論文名も言葉の説明も無く、同趣旨の式の頭が重複する（$\mathrm{B}_0$ / ${B_0}$）。教員の前提の地図も、PDF 版の論文を含むコースは全点「番号なし:」で始まる（TeX 版の式は「式 (6)」と印字番号で読める）。対象・支える主張の英語の生成文（「An equation of this paper defines $M$.」）と同文の 2 行（区別不能）も te-02 / te-04 / te-03 で残る。run: uxsim/runs/c-r25-doubt-r5/20261027T071839Z-88666（seq11・14）/ uxsim/runs/c-r25-doubt-r5/20261027T070623Z-23504（seq3〜5）/ uxsim/runs/c-r25-doubt-r5/20261027T070946Z-33422（seq2・5）。

第 6 周の再演: 残存（2026-10-27）。教員: 台帳の対象・支える主張が英語の生成文（「An equation of this paper defines $c_s^2$.」）、同文 2 行、支持線の文に英語の段の名前「Diagnostic / application」、前提の地図の点が「番号なし: $…$」（PDF 版を含むコース）。学生: 未検証の前提一覧と開幕が英語の序論の文・文字化け「�� ≈ 0.75」・文献キー「andre2010archetypal」（並びの入れ替わりは IK-1030）。run: uxsim/runs/c-r25-doubt-r6/20261027T122225Z-3508（seq3・5）/ uxsim/runs/c-r25-doubt-r6/20261027T122508Z-5219（seq5）/ uxsim/runs/c-r25-doubt-r6/20261027T123134Z-9179（seq11）。

第 7 周の再演: STILL（2026-10-28）。台帳の対象名・支える主張・合意リストが英語の生成文（An equation of this paper defines $c_s^2$. 等）で、同じ文面が支える主張に 2 行並ぶ。散布図は「（ほか、同じ文面の点: 点6・点8・点12）」と畳むが、未検証合意リストは同じ文面を名前の区別なく並べる（畳み方が 2 つの一覧で違う）。支持線の文「『Diagnostic / application』が同時に崩れると…」の段階名が英語の内部名のまま。学生の教材の「この節で使う数式」は TeX ラベル（eq:dcf・eq:modified_dcf）と同じ定型文、「（本文を特定できない要素）」。run: uxsim/runs/c-r25-doubt-r7/20261027T171356Z-51086（seq3・5・6）/ uxsim/runs/c-r25-doubt-r7/20261027T171640Z-52350（seq3・13）/ uxsim/runs/c-r25-doubt-r7/20261027T173022Z-59619（seq6）。

第 8 周の再演: STILL（2026-10-28）。前提の地図・合意リスト・ノード台帳・出典タブ・開幕・わたしの地図で英語の定型生成文（An equation of this paper defines …）・同文の行（点1 ほか同じ文面の点 4 つ・支える主張 2 行）・段階名「Diagnostic / application」・式の断片（番号なし: …）・文の途中切れ（(Fig. / 3b ) is reproduced…）。教材一覧と章見出しで PDF 版の題名が arXiv 番号のまま。run: uxsim/runs/c-r25-doubt-r8/20261027T223243Z-92707（seq3・5）/ 20261027T223539Z-94356（seq5）/ 20261027T223914Z-96688（seq3）/ 20261027T224211Z-98239（seq9・11・15・16）。

第 9 周（2026-10-31）: 支持線の切れ目の段名は日本語（『診断・応用』— 第 8 波）で GONE。散布図・合意リスト・支える主張の英語の生成文（「An equation of this paper defines $M$.」が同文 2 行・式の断片）は STILL。run: uxsim/runs/c-r25-doubt-r9/20261031T012052Z-34781（seq5）/ 20261031T003210Z-30688（seq3）。

第 10 周（2026-10-31・judge_doubt_r10）: STILL（同じコース・同じ行）。合意リストの 8・9 行目が同文「An equation of this paper defines $M$.」、原稿スタジオのノード台帳の支える主張にも同文 2 行（区別の手がかりなし）。run: uxsim/runs/c-r25-doubt-r10/20261031T052922Z-65583（seq3）/ uxsim/runs/c-r25-doubt-r10/20261031T053250Z-67645（seq5）。

## 第 11 周の再演（第 25 周・面 B 第 11 周、2026-10-31・judge_doubt_r11 — 固定コース 1bad7d9f・3 周連続の同じ対象）

STILL（3 周同じ）。ノード「理論の土台」の台帳の支える主張に「An equation of this paper defines $M$.」が 2 行同文で並び、合意リスト 8・9 行目も同文。散布図の点・リストの行は英語の生成文のまま。run: 99946 seq5 / 98534 seq3。
