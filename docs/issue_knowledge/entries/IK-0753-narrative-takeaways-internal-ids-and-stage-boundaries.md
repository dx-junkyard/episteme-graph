---
id: IK-0753
title: "narrative_annotator が受け取る主グラフの材料が人向けでない — teaching_takeaways は内部 ID と件数の機械文、「整合関係」段の記述は序論の背景文、「消去」段に論文のほぼ全式、段の間に往復辺"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-22
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/theory-graph.md"
  - "docs/features/graph_dialogue_review_design.md"
feature_context:
  realizing: "理論操作グラフの主グラフ（#308 の theory stage 集約）に、読み手向けの narrative 注釈と教育上の要点を付ける"
  layers: [pipeline_a, graph_review]
classification:
  axes:
    processing: [none]
    structure: [representation, aggregation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: low
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認済み（材料の部分）: narrative_annotator の input_builder が main ノードの材料に derivation_chain の機械文の takeaway と ID 列を渡していた。段の境界・往復辺の部分は IK-0844 に仮説のまま切り分けた。以下は起票時の見立て:
    仮説: 主グラフの main ノードは stage 単位で全 derivation を跨いで集約されるため、各段の材料
    （teaching_takeaways・description・成員の式）が「集約したもの」の機械的な要約（『Derive eq_… from eq_… via
    define -> derive_result』『Logical progression through N claims in section sec_X』）と、成員の和集合
    （消去 76 式・基盤 41 式）にしかならない。構造軸 representation = 段の説明文を人向けの材料として表す欄が無く、
    内部 ID と件数の連なりで代用している。構造軸 aggregation = stage への集約が論文の境界（模型・結果・到達点）を
    潰し、段の境界・順序（往復辺）が決まらない。接続軸 information = 部品層（component_assembly の 16 部品）の構造が
    主グラフの材料に渡っていない疑い（low）。処理軸 none / 統制軸 none。確定には normalizer が main ノードの
    teaching_takeaways / description を何から組むかと、stage 集約時の辺の向きの決め方を読む。
generalization:
  level: general
  general_form: "粗い集約の結果に説明を付けさせるとき、集約が保持するのが内部 ID と件数だけなので、説明の材料が人の読める形で渡らない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 M1 が narrative_annotator の材料（seq 2095 = AdS/dS 連星 PN 論文の主グラフ 5 段・辺 12、seq 2098 = トリスペクトル論文の主グラフ 6 段・辺 17）を読み、teaching_takeaways が内部 ID の機械文だけ・「整合関係」段の記述が序論の背景文・消去 / 解く段に式 52〜76 本・基盤段へ戻る往復辺・中心命題の到達点に当たる段が無いことに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_M1.md）。部品層の構造は主グラフに反映されていなかった。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: "narrative_annotator の材料を人の読める形で組み直した（input_builder.py）。main ノードは label / description / theory_object / 集約した step（equation_detail ノード）の description（atomic claim の本文）/ 印字式番号（eq_12 → (12)、内部の式 ID は落とす・列挙のみ）/ concepts で表し、linked_claim_ids / linked_equation_ids の ID 列は渡さない。derivation_chain の teaching_takeaway は矢印（->）・件数（N claims / equations / step(s)）を含む機械文を除き、全テキストから内部 ID トークンを外す。LLM はノード・辺を N1 / E1 の handle で指し、_parse_raw が内部 ID へ戻す。修復の検証メッセージも handle に置き換える。内部 ID の判定は A層の正本 internal_id_text.INTERNAL_ID_RE を表示側 display_projection.INTERNAL_ID_TOKEN_RE の逐語の写しにして（sec_ / section_ / fig_ / claim_ / derivation_ / support: などを追加）、両パターン文字列の一致を backend のガードレールで固定した。段の境界（成員の式が多すぎる段・往復辺・到達点の段の欠如）は本件で直していない — IK-0844 に分けた。"
  landed_in:
    - "src/episteme_graph/agents/narrative_annotator/input_builder.py"
    - "src/episteme_graph/agents/narrative_annotator/prompt.py"
    - "src/episteme_graph/agents/narrative_annotator/repair.py"
    - "src/episteme_graph/agents/internal_id_text.py"
    - "src/tests/agents/narrative_annotator/test_readable_material_ik0753.py"
    - "backend/tests/test_display_projection_guardrails.py"
  verification:
    methods: [guardrail]
    unverified:
      - "live LLM run not performed（実論文の主グラフで narrative が内部 ID・件数を含まないことの実測）"
      - "主グラフの description が序論の背景文・空になる件（normalizer の _build_main_description の材料選び）は材料の側では直していない"
      - "internal_id_text の語彙拡大が equation_semantics / contextual_explanation の実データで過剰に外さないか"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: >-
        採った部品は「規則を全経路が通る 1 箇所に置き、迂回できないことを検査で固定する」。固有の差分: A層は backend を import できないため、内部 ID の正規表現を A層側に 1 度だけ写し、写しと正本のパターン文字列の一致をガードレールにした。
related: [IK-0571, IK-0727, IK-0728, IK-0844]
view_of: []
history: []
---

## 課題

症状: narrative_annotator に渡る主グラフの teaching_takeaways が『Derive eq_… from eq_… via define -> derive_result -> …』
『Logical progression through N claims in section sec_X』の機械文だけで、読み手向けの材料にならない（学習者に漏れる形）。
「整合関係」段の description が序論の背景文、「消去」段に論文の式 76 本全部・「基盤」段に 41 本と段の境界が無く、
段の間に往復辺（消去 → 基盤 → 消去）があって読む順序が決まらない。中心命題の到達点（2PN 結果・共形代数の確認）に当たる段が無い。
原因（仮説）: stage 集約が成員の和集合と内部 ID の要約しか保持せず、説明の材料を人の読める形で表す欄が無い。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_M1.md（seq 2095・2098）。

同根の再発: uxsim/runs/radar_loop/BRAIN_NOTES_Q1.md（seq 2554）— teaching_takeaways が 'Derive eq_... from eq_... via define -> ...' の内部 ID の羅列、主グラフに閉路（土台と整合関係の段の相互参照）、方程式系の段の description が付録の ρ_A=0 条件、3 段は description が空。

追加の出所（2609.35757・TeX 経路・頭脳 T1）: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2941 — main 5 ノードのうち description 3 件が空・2 件が同一文、teaching_takeaways が内部 ID の連鎖、15 辺のうち後段→前段の 7 本で読む順が成り立たない、消去段 91 式・基盤段 56 式）。

追加の出所（2610.08751・頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3290・応答は後任が採用 — 主グラフ 5 ノードの description が段と無関係（Theory basis に Jeffreys の緩和策の文・Consistency relation に BAO の背景文）か空、teaching_takeaways が内部 ID の列、辺が循環（0001⇄0002・0001⇄0004・0004⇄0005）、Consistency relation に主張 175 件。導出の出発点の誤りは IK-0811）。

追加の出所（頭脳 X2・是正前の run（seq 3290〜3314））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3290 — Theory basis の description が先行の緩和策（Jeffreys）で中心命題に対応する段が無い、teaching_takeaways が内部 ID 列と「Logical progression through 34 claims in section sec_12」、整合関係ノードに主張 180 件超、辺が双方向）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z1）: uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3643 — teaching_takeaways が「Derive eq_post_laplace from eq_eqcand_inline_tex_b36_80_e2081ccd … via approximate -> …」「Logical progression through 27 claims in section sec_1」の内部 ID・件数・sec_N。プロンプトは takeaway の再利用を指示するので内部 ID が narrative に入りうる。Equation system / Elimination の description が空、Consistency relation に序論の背景文、辺が双方向）。

## 発見の観点

narrative_annotator の入力の段ごとの材料（要点・記述・成員の式・辺）を、論文の構成と部品層の部品に突き合わせた（data_inspection）。

## 解決の観点

材料の表現を変えた（representation_change）: 集約が保持する内部 ID と件数の機械文を材料に入れず、ラベル・記述・atomic claim の本文・印字式番号で組み、ノード・辺は handle で指す。プロンプトに内部 ID トークンが現れないことと、A層と表示側の正規表現が一致することをテストで固定した（guardrail_fix）。段の境界・辺の向きは IK-0844。

## 一般化

粗い集約に説明を付けさせると、集約の材料が内部 ID と件数しか持たず、説明が人向けにならない。辞書の型
information-dropped-as-unrepresentable の近傍（部品層の構造を渡していないなら available-but-unwired）。
