---
id: IK-0869
title: "記号をタップしても TeX 経路の論文で定義が出ない — 記号の登録簿は「定義の式はあるが逐語の引用が無い」記号を defined として持つのに、記号の照会は逐語の文が無い候補を捨てて「この論文には定義の記述が見つかりませんでした」と言い、教材に定義式（F_EOS = {P(k)}）が見えている記号でも定義が無いと答え、配信済みの式からの探索も登録が無い記号でしか走らない"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "学生が教材の式の記号をタップし、その記号がこの論文で何を表すか（定義と概念）を確かめる"
  layers: [concept_registry, frontend_learning_ui, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [contract]
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
    仮説: 照会の応答は P・ε・r・F_EOS の 4 記号とも facts が FACT_NO_DEFINITION だけで、これは
    backend/core/symbol_lookup.py:795-805 の「一致する記号行はあるが定義の候補が無い」分岐でしか出ない（登録の行は在る）。
    構造軸 representation: _definition_candidates（:496-560）は defining_equation_ids から候補を作るが、本文は
    definition_evidence_texts（逐語）か evidence 行の evidence_text からしか取らず、式の位置の行（:300-323、label・block だけ）
    は本文を持たないので、逐語の無い候補は :560 で捨てられる。一方 src/episteme_graph/agents/symbol_registry/builder.py:400-412 は
    「defined without quoted evidence text」を正規の状態として作る。接続軸 contract: 登録簿の出力契約（式だけの定義）と照会の
    入力契約（逐語が要る）が両立していない。さらに配信済みの式（chunks.formulas）からの探索 _formula_fallback は :718 の
    「登録の行が 1 つも無い」ときだけ呼ばれ、行はあるが定義が無いときは走らない。本文の言い換え（「正規化した対数圧力」
    「半径 r」）を定義として拾う経路はどこにも無い（A層の登録簿が散文の定義を取らない）。確定には砂場の
    knowledge_symbols_live の該当行（document ce667c63-…）の definition_status・defining_equation_ids・
    definition_evidence_texts を読めばよい。
generalization:
  level: repo_pattern
  general_form: "前段が『根拠の逐語は無いが対象は在る』状態を正規に作るのに、後段は逐語の無い候補を捨てて『無い』と言い切り、手元にある別の材料も見ない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    学生 st-02・st-03 が記号を 4 つずつタップし、全部「定義の記述が見つかりませんでした」で目標を諦めた（gave_up）。
    st-03 は教材の「この節で使う数式」に F_EOS の定義式が出ているのに定義が無いと言われた点を挙げた。応答の facts の出る
    分岐と登録簿の状態の作り方を突き合わせた。run: uxsim/runs/c-astro-r25-share-r1/20261022T124951Z-39393 seq14-17・
    20261022T125633Z-43511 seq14-17。
resolution:
  perspective: [carry_through, representation_change]
  note: >-
    照会側: 逐語の無い式だけの定義を捨てず「式 (3)で定義されています。」+ 定義式の本文（definition.latex）にした。登録の行に定義が無いときも配信済みの式から探す（登録あり用の文面）。ハーネス是正者の所見（画面が上付きを添字に結合して送る — r^2 の r が r_2、c_s^2 が c_s2）を app.js symbolTextFromToken で確認: msupsub の textContent が上付きと下付きを連結していた。画面は KaTeX の vlist の段の位置で上付きを落として下付きだけを添字にし、サーバは完全一致の後に symbol_key_fallbacks（べき・末尾の数字の添字を 1 段ずつ落とす完全一致）で照合し直す。knowledge_symbols_live の該当行は砂場を読めないので未確認（仮説の確定はしていない）。
  landed_in:
    - "backend/core/symbol_lookup.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_share_round25_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の knowledge_symbols_live の行で F_EOS・P・r の定義の有無を実測していない"
      - "KaTeX の上付き・下付きの判別は style.top の閾値（-2.8em）に依る — 実機の描画で確かめていない"
      - "A層（登録簿が本文の散文定義を逐語として拾う）は別の判断として残した"
related: [IK-0518]
view_of: []
history: []
---

## 課題

症状: TeX 経路の論文で記号をタップすると、どれも「この論文には定義の記述が見つかりませんでした」。教材に定義式が
見えていても同じ。原因（仮説）: 登録簿は逐語の無い式だけの定義を作るが、照会は逐語の無い候補を捨てる。配信済みの式からの
探索も登録が無いときしか走らない。

## 発見の観点

学生ペルソナの通し受講（reproduction）。応答の文が出る分岐を辿った（trace_walk）。

## 解決の観点

未解決。照会側で式だけの定義を表す形を持たせ、手元の式を見る。DB の行を読めば原因が確定する。

## 一般化

前段が根拠の薄い状態を正規に作り、後段がそれを「無い」に倒す型。確かめ方は、定義式だけを持つ記号・逐語を持つ記号・
登録の無い記号の 3 つでタップし、それぞれ定義・定義・式の位置が出ることを見ること。

## 第 2 周の再演（第 25 周・面 A 第 2 周、2026-10-23）

一部解消・一部残存。定義式だけを持つ記号（F_EOS）は定義が返るようになった。本文に散文で意味が書かれている記号（半径 r・
圧力 P）は、生応答でも `definition: null` のまま（「この論文には定義の記述が見つかりませんでした」）。この是正は散文定義の
取り込みを範囲外にしたためで、照会の材料に同じ論文の主張本文での言及を足す是正は IK-0893 に引き継ぐ。F_EOS の定義文が
英語の逐語の切れ端（「the EOS feature vector ... to stellar profiles」）で返るのは A層の英語の生成文の族（IK-0571）。

第 2 波（2026-10-23）で是正: r・P のように登録簿に定義の文が無い記号は、同じ論文の主張本文での使われ方を出所付きで添える（IK-0893。定義と断定しない）。散文の定義を A層が登録簿に書かない点は A層の判断として残る。
