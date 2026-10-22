---
id: IK-0728
title: "component_assembly の refiner が、名前・式・責務の食い違う子部品を無関係な親に重複して量産する"
status: open
recorded_at: 2026-10-16
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/architecture/agent_inventory_and_refactoring_2026-09-10.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "再利用可能な理論部品を、操作の単位（定義・導出・連立・適用）の子部品に分割して理論操作グラフと二層説明に渡す"
  layers: [pipeline_a, hierarchical_explanation]
classification:
  axes:
    processing: [none]
    structure: [decomposition, representation]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: refiner の分割単位（何を 1 つの子部品と数えるか）が定義されておらず、連立系の合成主張に付いた式約 50 本を
    そのまま子の入力に束ね、同じ導出名の子（「Derive: I, P_{fid}, T(...)」「Derive: K_+, K_-, K_Dom,Sub」）を、その式を
    共有するだけの無関係な親部品ごとに立てている。構造軸 decomposition = 子部品の粒度が式・操作・責務のどれでも揃わず、
    写像の部品に application / equation_system の子が立つ。構造軸 representation = 子の名前が束ねた式の中身と対応する
    表現になっていない（「赤方偏移空間写像の定義」の子の式がバイアス展開）。接続軸 target = 子に渡る式・主張の対象が
    親とずれる（DSL ノードのガウス共分散が別の部品に写像され交差辺が誤った相手を指す）。処理軸 none / 統制軸 none
    （順序・予算の問題ではない）。確定には refiner の入力（親部品の式・主張の母集合）と、子の名前・式の割り当て規則を
    読み、同じ式群が複数の親に配られる経路を確認する。f(r) 等の記号同一視による誤結線（IK-0717）が式の束に混ざって
    いる可能性も切り分ける。
generalization:
  level: repo_pattern
  general_form: "分割の単位を定めないまま大きな入力集合を子に配るため、名前と中身の食い違う子が無関係な親に重複して立ち、後段がその子を扱えない"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 M0 が component_graph（seq 2091・2094）と contextual_explanation（seq 2118・2121）の材料を読み、refiner 後の子部品の重複・名前と式の食い違い・式約 50 本の束に気付き、該当の子は説明を skip した（出所: uxsim/runs/radar_loop/BRAIN_NOTES_M0.md）。振り分け（seq 2082・2085・2088）でも写像部品に application / equation_system の子が立ち、式の無い equation_system の子が出ていた。"
resolution:
  perspective: [pending]
  note: "未解決。子部品の単位を操作（operation）と式の backing で定義し、親が式を共有するだけでは子を複製しない。名前は束ねた式から導き、名前と式の食い違いは review_required で残す案。"
  landed_in:
    - "docs/pipeline/agents.md"
related: [IK-0717, IK-0733, IK-0755]
view_of: []
history:
  - date: '2026-10-17'
    field: resolution
    from: refiner 側の是正なし
    to: refiner 側の是正 1 件（同一 step の複製停止・unknown_specific_operation の子を立てない）
    reason: "頭脳 W1（2610.08751・seq 3289）で同じ「Derive: Δn…」（入力式 12）が comp_003/004/011/012 に複製されていた。_assign_step_owners で同じ (operation, 入力式集合, 出力式集合) の持ち主を 1 つに決め（導出チェーン直結 → 共有式の数 → 組立順）、他の親には立てない。unknown_specific_operation は汎用扱いで子を作らない（component_refiner.py・test_refiner_radar_w1_regressions.py）。子の名前と束ねた式の食い違い・記号同一視の誤結線（IK-0717）・責務分割（__rN）の子の型が機械的に並ぶ件（IK-0813）は残るため open のまま"
---

## 課題

症状: refiner の分割子「Derive: I, P_{fid}, T(...)」が comp_003/005/006/007 に、「Derive: K_+, K_-, K_Dom,Sub」が
comp_002/006/007/009 に重複して付く。子の入力は連立系の合成主張の式約 50 本で、comp_003__op1 は名前「赤方偏移空間写像の
定義」なのに入出力がバイアス展開の式。写像の部品に application / equation_system の子が立ち、式が 1 本も無い
equation_system の子もある。contextual_explanation はこの子を「範囲が支離滅裂」として skip した。
原因（仮説）: 子部品の単位が定義されず、親に結びついた式群を丸ごと配っている。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_M0.md（seq 2082・2085・2088・2091・2094・2118・2121）。

同根の再発: uxsim/runs/radar_loop/BRAIN_NOTES_Q1.md（seq 2551 / 2563 / 2566）— 同一の汎用 op『Derive: (\Delta x)^3, A^{ij}, G』（入力式 35 本）が 6 部品に複製され、DSL ノードがそこへ写像される。説明では名前と式群が噛み合わず skip され、op 名『Unknown specific operation』が学習者向け文に出る危険がある（IK-0755 の機械文と隣接）。

追加の出所（2609.35757・TeX 経路・頭脳 T1）: uxsim/runs/radar_loop/BRAIN_NOTES_T1.md（seq 2938 — 分割単位 comp_002__op* / comp_004__op* / comp_011__op* の名前が「Derive: D/D\tau, M, N」の記号列、op5・comp_004__op2・comp_011__op1 の 3 単位が同じ 91 式を抱え、大離隔因数分解の単位が付録の eq_eqn_app_gaussian_generator や PBS の eq_eqn_pbs_Rdelta を出力に持つため io 一致がほぼ偽陽性。DSL ノードは分割後の最後の op（comp_002__op6）に一律で載り、DSL 辺の端点が意味と合わない。seq 2953・2956 — comp_011__op* の 3 単位が同じ上位主張を共有し段階差が読めない）。

追加の出所（頭脳 T0）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2940 — comp_002（因数分解）が「Evaluate condition / Apply to context / Unknown specific operation / Transform representation」の汎用 op 6 個に機械分割され、付録の式（gaussian_generator・tex_b189）や別節の式（eqn_ansatz・tex_b132/134）まで割り当たる。seq 2955 — comp_004__op2 の uses_equation に無関係な式が並び、頭脳は式に触れず主張だけで説明した）。

追加の出所（頭脳 T2）: uxsim/runs/radar_loop/BRAIN_NOTES_T2.md（seq 2939 — op 分割の comp_002__op5 / comp_004__op2 / comp_011__op1 が論文の式ほぼ全部（約 80 本）を入力に持ち、名前が「Derive: D/D\tau, M, N」。seq 2954 — 同じ上位 3 主張が op1〜op6 全部に重複付与され、使用式は op ごとに雑多）。

追加の出所（2610.08751・頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3289 — Jeffreys の部品 comp_004 が 6 演算に割られ op1 の入力に AP の式が入る。同一の 12 式を入力に持つ「Derive: \Delta {\bm n}, \Pi({\bm \Theta})…」が comp_003/004/011/012 の 4 箇所に複製。DSL 辺 e5/e7/e8/e9/e11 の行き先がすべて汎用演算 comp_012__op3 に吸われる。宣言依存の向きが逆のもの（comp_006__r2 → comp_006__r1）もある。seq 3286 — 子の名前は親ラベルの英語の機械生成）。

追加の出所（頭脳 X1・是正前の run（seq 3289〜3310・2026-10-17 03:00〜03:11））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3289 — 「Derive: \Delta {\bm n}, \Pi({\bm \Theta}), …」が comp_003/004/011/012 の 4 部品に同名・同入力 12 式・同出力で複製。seq 3295・3298 — comp_002 の 3 子・comp_011 の 5 子がすべて同じ supports_claim を共有し、子ごとの役割差が式でしか分からない）。

追加の出所（頭脳 X0・是正前の run（seq 3288〜3312））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3297 — comp_006__r1 と __r2 の upper_context が完全に同一。comp_007__op1 の uses_equation に主題外の式（θ 分割・Gaussian 尤度・TCM）が混入）。

追加の出所（頭脳 X2・是正前の run（seq 3290〜3314））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3296 — comp_003__op4 と comp_004__op6 の local_text が同一「Derive: \Delta {\bm n}, …」で別部品の子が同じ顔になる。comp_004__op1 の lower_context に AP・多重極の無関係な式 = IK-0811）。

追加の出所（2610.08751・頭脳 Z2）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3629・3632 — 同じ eq_tex_b70（q⊥/q∥ の定義）が comp_007 と comp_008 の両方の振り分け対象に出て、両方で割り当てると 1 つの式が 2 部品に二重所属する。同じ主張が 2 つの atomic（claim_span_003_48_sub01 / claim_span_001_81_sub02）で重複）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z0/Z1）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3639 — DSL の e7（Jeffreys 事前分布 n7 → χ² の INHIBITS）が TCM の部品 comp_014__op2 に写り、Jeffreys の部品 comp_005 に結ばれない。comp_014__op1 の名前が記号の羅列。comp_005 の outputs に入力側の eq_post_laplace）、uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3658 — 分割子 __r1/__r2 が親と同じ summary で、label 末尾だけが違う）。

## 発見の観点

refiner 後の部品の名前・式・親を、component_graph と contextual_explanation の入力で突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

分割の単位が無いまま入力集合を配ると、名前と中身の合わない子が重複して増える。辞書の型 unit-of-work-undefined。
