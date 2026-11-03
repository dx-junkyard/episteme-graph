---
id: IK-0757
title: "equation_semantics の文脈で式 ID がラベル由来（eq_eqn_*）とブロック由来（eq_tex_bNN）の 2 形式で混在し、LLM が input/output_equation_ids に eq_tex_bNN 形を推測で作って解決しないリンクになる"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/overview.md"
  - "docs/features/knowledge_objects_design.md"
feature_context:
  realizing: "TeX 経路で取り込んだ式に意味役割を付け、式どうしの入出力関係（input/output_equation_ids）を解決できる ID で張る"
  layers: [pipeline_a, knowledge_objects]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: TeX 経路ではラベルのある式は \label 由来の ID（例 eq_eqn_avgpressure）、ラベルの無い式はブロック由来の ID（eq_tex_bNN）を
    受け取り、同じ論文の中で式 ID の規則が 2 つ並ぶ。しかも文脈（前後のブロック）に出る式は ID を伴わないため、LLM は参照先の式の ID を
    知らずに eq_tex_bNN 形を推測で作り、input/output_equation_ids に入れる（seq 2489: eq_eqn_avgpressure の入力に eq_tex_b49 を推定で指定）。
    構造軸 representation = 式を指す識別子の形が 1 つの規則に揃っていない。接続軸 target = 文脈の式が ID を伴って渡らず、参照の宛先が
    LLM に届いていない。処理軸 logic（確信度 low: 推測された ID が実在しないことは突き合わせていない）。統制軸 none。
    型は IK-0719（claim ID の 2 名前空間）と同じ id-namespace-conflated と読んだ。IK-0724 は内側スコープの番号の衝突で原因が別。
    確定には、equation_semantics の入力 builder が文脈の式に ID を載せているか、eq_tex_b49 が equations.json に実在するかを
    該当 run の artifact で確かめる。
generalization:
  level: general
  general_form: "同じ対象の識別子が由来ごとに別の規則で作られ、文脈に ID が添えられないため、生成側が参照先の ID を推測で作って解決しない参照になる"
pattern: id-namespace-conflated
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 P2 が equation_semantics（seq 2489・eq_eqn_avgpressure）の入出力を読み、式 ID がラベル由来とブロック由来で混在し入力に eq_tex_b49 が推定で指定されていることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_P2.md）。"
resolution:
  perspective: [carry_through, guardrail_fix]
  note: "equation_semantics の入力に近傍の式の一覧 neighbor_equations（同じ節を優先・上限つき・文書順、各要素は解決可能な equation_id 付き）を載せ、プロンプトに「入出力の ID はこの一覧から選ぶ」1 文を足した。応答の input/output_equation_ids は restrict_io_links_to_neighbors が一覧外の ID を外し、warning と review flag io_id_not_in_neighbor_equations で残す（捨てた事実を落とさない）。ラベル由来（eqn: / eq:）の混在は原文の \\label の書き方そのもので、ID 規則は変えていない。"
  landed_in:
    - "src/episteme_graph/agents/equation_semantics/input_builder.py"
    - "src/episteme_graph/agents/equation_semantics/schema.py"
    - "src/episteme_graph/agents/equation_semantics/prompt.py"
    - "src/episteme_graph/agents/equation_semantics/validator.py"
    - "src/episteme_graph/agents/equation_semantics/agent.py"
    - "src/tests/agents/equation_semantics/test_neighbor_equations.py"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "実 LLM での 2609.35757 の再演（近傍一覧が ID の推測・持ち込みを減らすか）"
related: [IK-0719, IK-0724]
view_of: []
history: []
---

## 課題

症状: equation_semantics の出力で、eq_eqn_avgpressure の input_equation_ids に eq_tex_b49 が LLM の推測で入っている。
同じ論文の式 ID がラベル由来（eq_eqn_*）とブロック由来（eq_tex_bNN）の 2 形式で混在する。
原因（仮説）: TeX 経路でラベルのある式とない式の ID 規則が違い、文脈に出る式は ID を伴わないため、LLM は参照先の ID を知らずに作る。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_P2.md（seq 2489）。

追加の出所（2609.35757・TeX 経路）: uxsim/runs/radar_loop/BRAIN_NOTES_S1.md（seq 2839・2845・2848 — 隣接式は本文に label=eqn:… 付きで載るだけで equation_id 一覧が渡らず input/output_equation_ids を材料から埋められない）、uxsim/runs/radar_loop/BRAIN_NOTES_S2.md（seq 2837・2843・2852・2855 — 同じ。2837/2843 で頭脳が命名規則から推測した ID はラベル持ち式で誤りの可能性が高い）。

追加の出所（頭脳 S0）: uxsim/runs/radar_loop/BRAIN_NOTES_S0.md（seq 2838・2841・2850・2856・2859・2880・2892 — 前後の式に equation_id が添えられず label からの命名規則推測になる。label の接頭辞が eqn: と eq: で混在、ハイフン入り label、label 無しは文字列「None」= IK-0785）。

追加の出所（頭脳 T1）: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2875・2884・2887・2893・2902 — 本文の「Equation (4.4)」「(A.4)」「(A.12) and (A.13)」は TeX の \eqref 解決済み番号で出るが番号と equation_id の対応が材料に無く、label=eqn:app-xy-generator の次式も ID が添えられないため output/input_equation_ids を埋められない）。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2868・2871・2874・2883・2892・2898 — 前後の式に equation_id が無く、本文の印字番号「Equation (1.1)/(4.4)/(A.16)」と ID の対応も無い。label の接頭辞 eqn: と eq: の違い、ハイフン・下線入り label で命名規則の推測もできず output_equation_ids を空にした）。

追加の出所（頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2873・2876・2879・2882・2885・2888・2891・2894・2897・2903・2921 — 隣接式は block_id とラベル文字列だけで equation_id が無く、印字番号「Equation (4.4)/(A.4)/(2.2)」との対応表も無いため入出力をラベル規則で推定した。seq 2927 — 同じブロック内の inline 候補どうしの ID も材料に無い）。新しい事実（seq 2915・2933）: ラベル「eqn:Ps_」「eqn:Bs_」の末尾の下線は実際の ID では落ちて eq_eqn_Ps / eq_eqn_Bs になり、頭脳がラベルから推した規則（eq_eqn_Ps_）と一致しない — 材料に ID が無い限り規則推定の ID は誤りうる。

追加の出所（頭脳 W0）: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3249 — Neighbor に eq_tex_b45 と eq_post_laplace が並び、ID が block 由来とラベル由来の二系統。seq 3252 — 本文の「Eq. (6)」「Eq. (7)」と式の対応表が無い）。

追加の出所（頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3253 — 本文の「Eq. (6)」「Eq. (7)」と equation_id の対応が材料に無い。Neighbor にラベル無しの eq_tex_b68/b70 とラベル由来の ID が混在。seq 3280 — 定義式 eq_tex_b70 が近傍窓の外で input に結べない = IK-0793）。

追加の出所（頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3245・3251 — 式 ID がラベル有りは eq_<label>・無しは eq_tex_b<N> の 2 系統で、Next Text Blocks では「label=eq:post_laplace」が行頭に出る）。

残る観察（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3573・3576・3579・3588 — inline 式の neighbor_equations が別節の事後分布式 8〜10 本（節 4 の eq_Pgg・eq_ctr_terms まで）で、対象の式と関連が無い。近さの窓が inline 式では式の位置を中心にしておらず、窓が広すぎる側に外れる。seq 3582 — 式参照は印字番号「Equation (2.8)」だけで式本体が無い）。

残る観察（頭脳 Z2）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3572・3602・3623・3677・3719 — 「Equation (2.5) / (2.12) / (3.4) / (4.4) / (A.16)」の番号から式への対応表が材料に無い。seq 3587 — where 節の定義（項を名付ける）を入出力の語彙で表せず「出力」で表すしかない）。

解消の確認と残存（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3603 — 近傍式が節内の 2 本に絞られ窓が効く。一方 seq 3573〜3591 の inline 式では別節の式が並び、窓の効き方が式の位置で揺れる。seq 3606 — 付録で近傍式が無くても欄が空で出る）。

残る観察（頭脳 AA2・2609.35757 の再解析 run 54e85a2a）: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（seq 3740・3743・3845・3848・3851 — 本文の「Equation (2.8) / (2.2) / (2.5)」の番号から式への対応が equation_semantics の材料に無い。印字番号（printed_number）は claim_qualification の Adjacent 区画には付いたが、Equation Context / Neighbor Equations には label しか無い。seq 3848・3851 — label が空のとき「label:」の空行が出る。seq 3857 — label「eqn:Ps_」の末尾の下線（原 TeX の label 名と見られる））。

## 発見の観点

実際のステージ入出力の式 ID と文脈の式を突き合わせた（data_inspection）。

## 解決の観点

文脈の式に解決可能な ID を添えて閉世界の候補として提示し（carry_through）、候補外の ID は検証で外して review flag に残した（guardrail_fix）。eq: / eqn: の接頭辞の混在は原文由来なので ID 規則の統一はしていない。

再解析での観察（reproduction_rerun・頭脳 X2・再解析 run 40ecf8df（seq 3317 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3347 — 本文の式番号参照が「Eqs. (10)--(11)」と解決されて出る）。equation_semantics の近傍一覧で番号と equation_id が結べるかは、この run の材料では確かめていない。

解消の確認（reproduction_rerun・頭脳 Y0/Y1・再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md（seq 3525 — Neighbor Equations に近傍式の ID が入り、前後の式本体も TeX で渡る）、uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3530・3548 — label 行が添えられ、本文の「Eq. (6)」「Eq. (11)」「Eq. (14)」がどの式か読める）。残る観察: 番号から equation_id を引く対応表は無く、label 行からの推測。近傍リストの latex_head が短く（先頭 60 字）、eq_tex_b45 と eq_post_laplace のような近い式を区別できない（seq 3524・3527）。

## 一般化

由来ごとに別規則の識別子と、ID を伴わない文脈が重なると、生成側が参照を捏造する。辞書の型 id-namespace-conflated（IK-0719 と同型、IK-0724 は別原因）。
