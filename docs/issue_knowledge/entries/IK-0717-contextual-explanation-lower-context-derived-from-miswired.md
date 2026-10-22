---
id: IK-0717
title: "二層説明（contextual_explanation）の材料で式の lower_context.derived_from が誤配線され、同じ 2 要素がほぼ全式に一律に張られ、向きが逆の導出元も混ざる"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/features/hierarchical_context_explanation_design.md"
  - "docs/pipeline/agents.md"
  - "docs/pipeline/theory-graph.md"
feature_context:
  realizing: "式ごとの文脈的な説明を生成するために、その式が何から導かれたか（下位の文脈）を材料として LLM に渡す"
  layers: [hierarchical_explanation, pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
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
    仮説: derivation_chain の決定論フォールバック（n_fallback_* の逐次 CORRELATES 鎖）か、claim 交差の粗い突合が、
    「導出元」として材料の lower_context.derived_from にそのまま入っている。同じ 2 要素（dS の Jost 変数・並進生成子の主張）が
    座標系・計量・波動方程式などほぼ全式に一律に付き、保存複素ベクトル ← 2PN 共役運動量の展開のように向きも層も逆のものがある。
    一方で同じ教材の別バッチ（次数下げ・重心の運動方程式）では妥当な derived_from も出ており、全経路が壊れているのではなく
    特定の由来の辺が混ざると読める。別の仮説（頭脳 L2・seq 2036）: derived_from が記号名の一致で張られ、SymbolRegistry が記号の
    スコープ（節・付録・dS/AdS の別）を見ていない — f(r) を定義する式が dS 用（Jost 変数）と AdS 用の 2 本あり、f(r) を使うほぼ
    全式に両方が付く／τ・z を定義する式（重心の運動方程式・Poincaré 座標）が τ・z を変数に使う再和恒等式に付く。処理軸 logic = 導出元を選ぶ突合の条件が粗い（仮説・確信度 low）。接続軸 meaning =
    前段の「相関・フォールバック由来の並び」が後段で「導出された」という意味に読み替えられている。構造軸 none = 受け皿
    （derived_from）自体は区別を表せる。統制軸 none。確定には、該当教材（c37f1e0a）の contextual_explanation 入力を組み立てる
    コードを読み、derived_from の各要素がどの artifact（derivation_chain の chain_type・fallback か、claim 交差か）から来たかを
    突き合わせる。
generalization:
  level: general
  general_form: "根拠の弱い関連（フォールバックの並び・粗い交差）が、後段の文脈材料で「由来」という強い関係として渡され、生成文の根拠を歪める"
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 L0 が contextual_explanation の要求（seq 1986・1992・1998・2010・2016）を応答する際に材料を読み、derived_from の一律・逆向きの接続に気付いて説明には使わず local_text だけで書いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md と uxsim/runs/radar_loop/brainL0_1986.json ほか）。頭脳 L2 が別教材（seq 2036）で記号名の一致による接続を見立てた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_L2.md）。"
resolution:
  perspective: [pending]
  note: "未解決。まず 2 つの仮説（フォールバック・粗い交差か、スコープを見ない記号名の一致か）を切り分ける。derived_from に入れてよいのは式の backing を持つ導出 step（equation_chain / mixed_chain・fallback でないもの）に限り、フォールバック由来・claim 交差由来の関連は別キー（関連の候補）に分けるか材料から外す案。"
  landed_in:
    - "docs/features/hierarchical_context_explanation_design.md"
related: [IK-0715, IK-0718]
view_of: []
history: []
---

## 課題

症状: 二層説明の材料で、式の `lower_context.derived_from` に同じ 2 要素（dS 動径問題の Jost 変数・並進生成子の主張）がほぼ全式に一律に
張られ、保存複素ベクトルが 2PN 共役運動量の展開から derived_from になるなど向きも層も逆のものがある。頭脳は説明に使えず局所本文だけで書いた。
原因（仮説）: derivation_chain の決定論フォールバック（n_fallback_* の CORRELATES 鎖）や claim 交差の粗い突合がそのまま「導出元」として
文脈材料に入っている。別の仮説（頭脳 L2）: derived_from が記号名の一致で張られ、SymbolRegistry が記号のスコープ（節・付録・dS/AdS）を
見ていない。f(r) を定義する式が dS 用と AdS 用で 2 本あり、f(r) を使うほぼ全式に両方が付く。τ・z を定義する式が再和恒等式に付く。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_L0.md（seq 1986 / 1992 / 1998 / 2010 / 2016）・uxsim/runs/radar_loop/BRAIN_NOTES_L2.md（seq 2036）。

追加の出所（2609.35757・TeX 経路・頭脳 T1）: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2944・2947・2953 — derived_from に後の節の式（母集団加重和・衛星条件付きガウス・2 パラメータ ansatz）が入り本文順と逆向き、λ の定義式 2 本が重複して並ぶ。lower_context の defines_symbol の text が定義ではなく導入句（「where, to very good approximation,」）。upper_context は式要素で全件空 = IK-0718）。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2943 — Φ_N の定義式 eq_tex_b26 がほぼ全式の derived_from に機械的に付く。seq 2946・2952 — 後の節の式（1 パラメータ減衰の局所化・W_p 型の ansatz）が導出元に入り向きが逆。upper_context は全要素で空 = IK-0718）。

追加の出所（頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2942・2945・2948・2951 — 「λ の記法」「Φ_N の定義」が P_O 選択則・SPCF の N 次項・W(0)=1 に導出元として機械付与、eq_tex_b97 には無関係な式 6 本、Π^[1] の定義の derived_from が Π^[1] を使う側の「δ = Tr Π^[1]」で向きが逆）。

追加の出所（頭脳 X0・是正前の run（seq 3288〜3312））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3291 — Bayes の定理（b37）が後段の Gaussian 対数尤度から derived_from になり、向きが不定。記号定義の text が「Writing the joint prior as」だけの断片。seq 3297 — comp_008 の lower_context に上向きの relation=supports が混入）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3654 — 部品 comp_001__r1 の upper_context の supports_claim に、部品自身の構成要素である記号定義の主張（「Theta denotes…」）が並び向きが逆。seq 3660 — comp_012 の lower_context に「supports component comp_010」。seq 3651 — eq_tex_b76 の導出元がカウンター項の式）。

## 発見の観点

実際に LLM に渡った材料を読み、derived_from の要素と式の内容の整合を見た（data_inspection）。

## 解決の観点

未解決。derived_from の供給元を式の backing を持つ導出 step に絞り、根拠の弱い関連は別の受け皿に分けるか落とす。

## 一般化

弱い関連が強い関係の名前で後段に渡ると、生成文が無根拠の由来を語る。辞書の型 fallback-fabricates-missing-link。
