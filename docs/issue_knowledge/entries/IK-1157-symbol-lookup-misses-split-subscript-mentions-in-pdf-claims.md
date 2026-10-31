---
id: IK-1157
title: "記号の吹き出しの「使われ方」の照合が、PDF の文字層で添字が割れた形（N c）を同じ記号と数えず、同じ論文の主張に「…at N c = 64 collocation points」と値の文があるのに「この論文には定義の文は見つかりませんでした」・使われ方 0 件で終わる"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "学生が教材の式の記号をタップし、その記号がこの論文で何を表すか（定義か、無ければ論文の中での使われ方）を確かめる"
  layers: [concept_registry, learner_experience_b, pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（砂場 DB の読み取りと関数の実行）: 固定コース 6be229e1 の PINN 論文は PDF 版 document 7ef9e487 が出典（IK-1117）。
    knowledge_symbols_live の N_c 行は両版（7ef9e487・TeX 版 ce667c63）とも definition_status = used・定義の文も根拠 ID も空。
    IK-0893 で足した第 3 の供給源 core/symbol_lookup.py::_claim_mentions は theory_claims_live の本文を _mentions_symbol で照合するが、
    PDF 版の主張は「L TOV enforces the stellar structure equations as soft ODE constraints at N c = 64 collocation points:」と添字が空白で割れており、
    _mentions_symbol(text, symbol_key("N_c"), _plain_symbol_re("N_c")) は False（TeX 版の「$N_c=64$」は True）。処理軸 logic（照合が割れた添字を
    同じ記号と見ない）。接続軸 information（上流の文字層で失われた添字の構造が、下流で「この論文には無い」という不在の事実文に化ける）。
generalization:
  level: repo_pattern
  general_form: "抽出の段で崩れた記法の言及を下流の照合が拾えず、資料にある情報が「見つからない」という不在の事実として利用者に出る"
pattern: upstream-loss-masked-downstream
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    第 25 周 面 D 第 11 周。st-08 が t11 の講義の後に記号 N_c を引き、「定義の文は見つかりませんでしたが、この記号は式 (4)の中に現れます」を読んで
    「教材には『固定された64点』、⚓ の英語の文にも『N c = 64 collocation points』とあるのに、どうして定義が無いことになるのか」と C 所見。
    吹き出しの応答・⚓ の主張・DB の記号行と主張行を並べ、_mentions_symbol を実行して割れた添字で落ちることを確かめた。
    run: uxsim/runs/c-astro-r25-studio-r11/20261031T102933Z-6346 seq6・12・13。
resolution:
  perspective: [single_point_fix]
  note: >-
    第 25 周 第 11 波（2026-10-31）: symbol_lookup._mentions_symbol の地の文の照合に、照合キーが「英字 1〜3 + _ + 英数字 1〜4」の記号の割れた形（N c / Nc / N_c）を足した（_split_subscript_re）。基底 1 字は後ろに関係記号か数値が続く形に限る。定義の判定・数式区間の照合は不変。
  landed_in:
    - "backend/core/symbol_lookup.py"
    - "backend/tests/test_round25_wave11_doubt_studio_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の N_c で吹き出しの使われ方に主張が並ぶことの再演"
related: [IK-0893, IK-0869, IK-1117, IK-0571]
view_of: []
history: []
---

## 課題

症状: 記号 N_c の吹き出しが「この論文には定義の文は見つかりませんでした」で使われ方も 0 件。同じ論文の主張と教材本文には N_c の値（64 個のコロケーション点）がある。

原因: PDF 版の主張の本文は添字が割れて「N c」になっており、使われ方の照合（IK-0893 で足した主張の供給源）がこの形を同じ記号と数えない。

## 発見の観点

学生ペルソナの通し受講で吹き出しと ⚓ の主張が食い違った（reproduction）。DB の記号行・主張行を読み、照合関数を実行した（data_inspection）。

## 解決の観点

第 11 波で是正（frontmatter の resolution）。以前の案: 照合の側で割れた添字を吸収し、定義の判定は変えない。

## 一般化

上流の文字層で失われた構造を下流の照合が前提にしていると、資料にある情報が「無い」と言われる。確かめ方: PDF 版と TeX 版の同じ論文で、添字付きの記号を引き、
使われ方の行が両版で同じ主張から出るかを見る。
