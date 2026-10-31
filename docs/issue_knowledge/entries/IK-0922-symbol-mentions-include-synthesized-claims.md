---
id: IK-0922
title: "記号の吹き出しの「この論文の主張では、次のように使われています」に、式から機械的に合成した主張（「In an equation of this paper, $R_*$ depends on $P(r)$.」）を論文の文と区別せずに並べ、しかも合成主張が先に選ばれやすい"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が式の記号をタップし、その記号がこの論文でどう使われているかを読む"
  layers: [concept_registry, learner_experience_b, knowledge_objects]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 meaning: core/symbol_lookup.py:988-1036 _claim_mentions は theory_claims_live を origin で絞らずに読み、ORDER BY は承認・作成順で
    origin を見ない。式由来の合成主張（origin='equation_synthesis'・本文は定型の英文）は論文の文ではないのに、固定文
    FACT_CLAIM_MENTIONS（「この論文の主張では」）の下に逐語の引用として並ぶ。IK-0790 と同じく、合成主張を論文の文として扱う別の消費者。
generalization:
  level: repo_pattern
  general_form: "生成した要約・合成文を、出所を区別せずに原文の引用として並べる"
pattern: projection-mistaken-for-source
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    学生 st-09 が r の使用例の 1 つ目を「論文の文というより機械的な言い換え」と記した。生応答の mentions と主張の取り出し SQL を突き合わせた。run:
    uxsim/runs/c-astro-r25-share-r3/20261023T033153Z-19784 seq7。
resolution:
  perspective: [canonical_source]
  note: >-
    core/schema.py に論文の文を持たない主張の出所の述語 NON_SOURCE_TEXT_CLAIM_ORIGINS（equation_synthesis）を置き、D層スコープ候補（IK-0790・doubt/scope_candidates/worker.py）と symbol_lookup._claim_mentions が共有する。_claim_mentions の SQL が origin を除外するので、合成主張は使用例に並ばず、合成主張しか言及が無ければ mentions を出さない。
  landed_in:
    - "backend/core/schema.py"
    - "backend/core/symbol_lookup.py"
    - "backend/core/doubt/scope_candidates/worker.py"
    - "backend/tests/test_share_round25_r3_fixes.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "合成主張を論文の文として扱わない述語を core/schema.py の 1 箇所に置き、IK-0790 の消費者と今回の消費者が同じ定数を読むようにした。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB での origin 除外の SQL（ANY に Python のリスト）"
      - "砂場の r・P の吹き出しの実表示"
related: [IK-0790, IK-0893]
view_of: []
history: []
---

## 課題

症状: 記号 r の使用例の 1 つ目が式から合成した英文で、論文の文のように引用符付きで出る。原因: 言及の取り出しが主張の出所（合成か原文か）を見ない。

## 発見の観点

学生ペルソナの通し受講（reproduction）。生応答から SQL まで辿った（trace_walk）。

## 解決の観点

是正済み（第 3 波）。合成主張を除く述語を 1 箇所に置き、記号の使用例の問い合わせがそれで絞る。

## 一般化

合成文を原文の引用に混ぜる型。確かめ方は、合成主張しか言及の無い記号・原文の言及がある記号をそれぞれタップし、前者は使用例が出ず、後者は原文だけが出ることを砂場で見ること。
