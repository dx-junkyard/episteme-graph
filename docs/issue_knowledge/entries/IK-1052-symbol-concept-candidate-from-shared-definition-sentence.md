---
id: IK-1052
title: "記号 → 概念の候補が「その記号の定義の文に概念の名前・別名が語として現れる」だけで立つため、ほかの記号も定義する 1 文に短い別名（TOV）が出ると無関係な記号が候補になる（PDF 版の F_EOS ⇄ 概念「TOV residual loss at collocation points」が確定・凍結された）— 一方、教材が使う TeX 版の F_EOS は定義の文に別名が無く候補にならず、版をまたいだ記号リンクも無いので学生の吹き出しに概念名が出ない"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員が記号を概念に結び、学生が記号の吹き出しでその概念名を読む"
  layers: [concept_registry, learner_experience_b]
classification:
  axes:
    processing: [logic]
    structure: [none]
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
    仮説: backend/core/library/symbol_concepts.py は knowledge_symbols_live の definition_evidence_texts のどれかに概念の名前・別名が語境界一致すれば候補を立てる。PDF 版の F_EOS の定義の文は「The structure network N TOV maps … the EOS feature vector F EOS = {P (kρ 0 )} … to stellar profiles」（同じ文が N_TOV の候補にも出る — uxsim/runs/c-astro-r25-share-r7/20261027T171357Z-51091 seq5）で、指揮者が足した別名「TOV」が N TOV に当たって F_EOS ⇄ TOV residual loss が候補になり、前提スクリプトが確定・凍結した（r7_prereqs.md「候補 4・確定 1」）。確定するには、PDF 版 F_EOS の definition_evidence_texts がこの文であることを砂場の knowledge_symbols_live で確かめる。処理軸 logic: 1 文に複数の記号の定義が並ぶとき、文のどこに別名があっても全記号に当てる。接続軸 target: 記号リンクは文書ごとの instance（PDF 版の F_EOS）に付き、学生の教材（TeX 版・1bad7d9f）の F_EOS は別の instance で、学習者の吹き出しは concept_ref:null（20261027T172824Z-59013 seq8・9）。構造軸 none: instance ごとのリンクは KN-2 の設計どおり。統制軸 none: 候補は教員の確定を経ている（確定したのは前提の運転）。
generalization:
  level: repo_pattern
  general_form: "複数の対象を述べる 1 文の中の語の出現を、その文に結び付いたすべての対象の性質とみなすと、別の対象についての語で誤った候補が立つ"
pattern: substring-match-false-positive
discovery:
  perspective: [trace_walk, data_inspection]
  note: >-
    st-04 (e) の吹き出しで F_EOS に概念名が出ず、教員画面の確定リンクを見ると PDF 版の F_EOS が「TOV residual loss」に結ばれていた。候補の定義の文（N_TOV と同じ文）と候補の規則（symbol_concepts.py）を突き合わせた。
resolution:
  perspective: [single_point_fix, carry_through]
  note: >-
    symbol_concepts.py: ①1 文がほかの記号も定義するときは照らす範囲をその記号を述べる部分（直前のほかの記号の後ろ〜直後のほかの記号の前）に絞り、記号名を伏せてから語境界一致を見る（_symbol_clause）。②別名がほかの記号名の一部（TOV ⊂ N_TOV）ならその別名では立てない（正式名は対象外）。③同じ論文の別の版で照合キーの同じ記号に、一方の版の候補・確定を candidate として写す（確定は人・見送りと取り消しは写さない）。複数の記号を定義する文から立った候補には事実文を添える。
  landed_in:
    - "backend/core/library/symbol_concepts.py"
    - "docs/manual/teacher/19-admin-knowledge-library.md"
    - "docs/features/concept_registry_design.md"
    - "backend/tests/test_share_round25_r7_fixes.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "同一性の候補の規則を版をまたいで運ぶ（写すのは candidate だけ）ために読んだ。固有の差分: 版の対は same_paper と照合キーの完全一致に限る。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の PINN の PDF 版 F_EOS の definition_evidence_texts が仮説どおりの 1 文かは未確認（cause_status hypothesis のまま）"
      - "既に確定・凍結された誤った組（PDF 版 F_EOS ⇄ TOV residual loss）は取り消していない（人の判断）"
related: [IK-0983, IK-1033, IK-1022]
view_of: []
history: []
---

## 課題

症状: 第 7 周の前に、指揮者が概念「TOV residual loss at collocation points」に別名「TOV」「TOV 残差」を足して［記号の候補を探す］を押すと候補が 4 件立ち、1 件（PDF 版の F_EOS）が確定・凍結された。F_EOS は EOS の特徴ベクトルで、TOV の残差損失とは別のものである。英語の学生 st-04 が教材（TeX 版の論文）で F_EOS をタップすると、定義は英語原文で読めたが概念名は出ない — 確定リンクは PDF 版の記号に付いていて、教材の TeX 版の F_EOS には候補も立っていない。

原因（仮説）: 候補の規則が、記号の定義の文に別名が語として現れるかだけを見る。PDF 版の F_EOS の定義の文は N_TOV の定義と同じ 1 文で、そこに「N TOV」がある。TeX 版の F_EOS の定義の文（the EOS feature vector …）には別名が無い。記号リンクは文書ごとなので、PDF 版の確定は TeX 版の教材に届かない。

## 発見の観点

経路の追跡: 学生の吹き出し（concept_ref:null）→ 教員画面の結びついた記号（PDF 版の F_EOS が確定）→ 候補の定義の文 → 規則。データの点検: 同じ文が N_TOV の候補にも出ていた。

## 解決の観点

第 7 波で是正（下の節）。当初の案:一致を見る範囲をその記号を定義する節に絞るか、複数の記号を定義する文であることを候補の行に事実文で出す。版をまたぐ同じ記号は候補を立てる（確定は教員）。前提の運転で候補を自動で確定しない（ハーネス側 H44）。

## 一般化

1 文に複数の主語があるとき、文単位の語の一致を各主語の性質とみなす型（substring-match-false-positive の一種）。確かめ方は、複数の記号を定義する文を含む論文で記号の候補を作り、各候補の当たった語がその記号の定義の節にあるかを見ること。

## 第 7 波の是正（第 25 周・面 A、2026-10-28）

第 7 波の是正（2026-10-28）: 記号を述べる部分だけを照らし（記号名は伏せる）、ほかの記号名の一部の別名では立てず、同じ論文の別の版の同じ記号には候補・確定を candidate として写す。原因の仮説（PDF 版 F_EOS の定義の文が N_TOV と同じ 1 文）は砂場で未確認のまま。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

未評価（記号の候補を探す手が場面に無く、候補の reason の新しい文を読む機会が無かった）。
