---
id: IK-0479
title: "再構成の問いを作る LLM 入力で、主張の概念・式・節見出し・出典文が全件空だった（同じ文書に出典の逐語引用・親の主張・節見出しが残っているのに、主張の行の空欄をそのまま渡していた）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
  - docs/features/reconstruction_loop_design.md §4.2
feature_context:
  realizing: "教員が承認した主張から、出典の文脈を踏まえた再構成の問いを自動で作る"
  layers: [reconstruction_r]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 問いを作る入力は主張の行の列（concepts / equation / source_scope / evidence_text）をそのまま写すだけで、
    分野未指定の解析ではそれらの列が空のまま保存される。同じ文書には、主張の元になった親の主張の本文、同じ block
    の文単位の逐語引用、出典チャンクの節見出し、主張を指す式の対応が残っていたが、入力の組み立てに配線されていなかった（information）。
    砂場の 10 件の出題依頼では照合材料が本文だけだった。処理・構造・統制の軸には原因が無い。
generalization:
  level: general
  general_form: "生成の入力を 1 つの行の列だけから組み立て、同じ対象について別の行に残っている根拠を渡していない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection, trace_walk]
  note: "砂場の出題依頼 10 件の答えキー JSON と、その主張の live 行・同じ block の逐語引用・親の主張を読み取り専用で突き合わせた。"
resolution:
  perspective: [carry_through]
  note: >-
    worker が候補を読んだあと、同じ文書の live 行だけから空欄を補う（claim_context.enrich_claim）。出典文は
    本文を丸ごと含む逐語引用 → 親の主張（atomic rewrite の元文）→ 語の重なりが十分な文単位の引用の順、節見出しは
    出典チャンクの見出し（崩れた見出しは使わない）、式は linked_claim_ids が主張・親を指すもの、概念は親の概念。
    既に値のある欄は上書きしない。
  landed_in:
    - backend/core/reconstruction/claim_context.py
    - backend/core/reconstruction/worker.py
    - backend/tests/test_ik0479_0482_recon_authoring_inputs.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（修正後に出題依頼を出し直していない。補完結果は砂場 DB の読み取りと純関数で確かめただけ）"
      - "概念は親の概念の引き継ぎだけで、親も空の主張（分野未指定の解析）では空のまま（IK-0483）"
related: [IK-0483]
view_of: []
history: []
---

## 課題

再構成の問いを作る LLM 入力で、主張の概念・式・節見出し・出典文が全件空だった（同じ文書に出典の逐語引用・親の主張・節見出しが残っているのに、主張の行の空欄をそのまま渡していた）

## 発見の観点

砂場の出題依頼 10 件の答えキー JSON と、その主張の live 行・同じ block の逐語引用・親の主張を読み取り専用で突き合わせた。

## 解決の観点

worker が候補を読んだあと、同じ文書の live 行だけから空欄を補う。出典文は本文を丸ごと含む逐語引用 → 親の主張 → 語の重なりが十分な文単位の引用の順、節見出しは出典チャンクの見出し、式は主張・親を指すもの、概念は親の概念。既に値のある欄は上書きしない。

## 一般化

生成の入力を 1 つの行の列だけから組み立て、同じ対象について別の行に残っている根拠を渡していない。
