---
id: IK-0893
title: "同じ記号の意味を、記号の吹き出しと部品の文脈が違う材料から答えて食い違う — 部品の文脈の主張には「pressure P as a function of energy density」と P の意味が書かれているのに、記号の吹き出しは記号登録簿だけを見て「定義の文は見つかりませんでした」と言い、境界条件 P(R*)=0 を「定義の式である可能性があります」と並べる"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が教材の式の記号をタップし、その記号がこの論文で何を表すかを確かめる"
  layers: [concept_registry, learner_experience_b, frontend_learning_ui, pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 接続軸 information: backend/core/symbol_lookup.py の照会は knowledge_symbols_live（記号登録簿）の定義候補と配信済みの式（_formula_fallback）だけを材料にし、同じ論文の主張（theory_claims_live の本文・atomic claim）や部品の concepts に書かれた記号の説明を見ない。同じ周の部品の文脈（EOS network map）は主張「A neural network represents the equation-of-state pressure P as a function of energy density」を出しており、学生は画面を往復してようやく P の意味を得た（st-06 seq86 と seq95）。処理軸 logic: 登録の無い記号で式の左辺に現れるものを「定義の式である可能性」と言うが、並ぶ式は P(R_*)=0・P(\rho_\ast)=3.06 MeV/fm^3（境界条件・数値）で、左辺に関数の値が来る式を定義と読んでいる。確定には、該当 document の knowledge_symbols_live の P 行の definition_status と、主張本文の記号の言及（$P$ / P）を突き合わせればよい。r も本文に「半径 $r$」とあるが登録簿に散文の定義が無い（IK-0869 で A層の判断として残した部分）。
generalization:
  level: repo_pattern
  general_form: "同じ問い（この記号は何か）に、画面ごとに別の材料から答え、片方の材料にある答えを他方が『無い』と言う"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    学生 st-06 が記号 P をタップして「定義の文は見つかりませんでした」と読み、続けて開いた部品の文脈の主張に P の意味があるのを見つけて「画面間で記号の確かめられ方が食い違う」と記した。応答の facts・equations と部品の文脈の claims を並べた。run: uxsim/runs/c-astro-r25-share-r2/20261022T220149Z-75546 seq8・seq16。
resolution:
  perspective: [carry_through, single_point_fix]
  note: >-
    core/symbol_lookup.py に第 3 の供給源 _claim_mentions を足した（同じ論文の theory_claims_live の本文のうち、記号を $…$ の数式区間か地の文の 1 トークンとして含む文・最大 2 件・数式区間を割らずに切る）。登録簿に定義の文が無い結果にだけ mentions と固定文 FACT_CLAIM_MENTIONS（「定義の文ではありません」）を添え、定義と断定しない。登録の無い記号でも言及があれば available。式の左辺による定義の推定は _lhs_is_definition_form で P = … / P(ε) = …（1 文字の変数の引数）に限り、P(R_*) = 0 のような値の代入を定義に数えない。決定論・LLM 0 回。app.js の吹き出しは mentions を数式付きで描く。
  landed_in:
    - "backend/core/symbol_lookup.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_share_round25_r2_fixes.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "記号の意味の材料を部品の文脈と同じ主張本文から引き、画面ごとに別の材料で答えない形に寄せた。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB で r・P・F_EOS・L_BC をタップしたときの見え方（第 3 周の st-06 (e)）"
related: [IK-0869, IK-0870]
view_of: []
history: []
---

## 課題

症状: 記号の吹き出しは「定義の文は見つかりませんでした」と言い、境界条件の式を定義の候補として並べる。同じ論文の部品の文脈の
主張にはその記号の意味が書かれている。原因（仮説）: 記号の照会が記号登録簿と式だけを見て、主張の本文を見ない。左辺の推定が
関数値の代入を定義と読む。

## 発見の観点

学生ペルソナの通し受講（reproduction）。吹き出しの応答と部品の文脈の主張を並べた（data_inspection）。

## 解決の観点

第 2 波（2026-10-23）で是正。答えの材料を画面ごとに持たず、記号の意味の供給源に主張の言及を足す（出所を付けて、定義と断定しない）。

## 一般化

同じ問いを複数の画面が別々の材料で答える型。確かめ方は、主張にだけ意味が書かれた記号・登録簿に定義がある記号・どこにも無い
記号の 3 つで吹き出しと部品の文脈を開き、両者が同じことを言うか、言わないなら理由が出ることを見ること。
