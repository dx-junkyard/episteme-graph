---
id: IK-0997
title: "学習チャットに渡す出典の抜粋のほぼすべてに「（この箇所の式: 番号のない式［AI復元］…）」が付く — 印が式ごとではなく抜粋ごとに、どの式かを名指さずに付き、TeX 経路の原文の式を含む抜粋にも付くため、AI は原文の式まで復元として扱い、学生向けの回答が「式は AI の復元で原文と照合できていない」と言う"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が学習チャットで式を含む論文の記述について尋ね、式が原文のものか AI の復元かを正しく知る"
  layers: [rag_chat, pipeline_a, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [condition]
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
    仮説: 構造軸 representation — 出典の抜粋に添える式の事実（core/formula_facts.py・IK-0577）が、チャンクの formulas のうち抜粋に現れる式を名指さず「番号のない式［AI復元］」の 1 行で表し、どの式に掛かるかを持たない。接続軸 condition: TeX 経路のチャンクの formulas に reconstructed が立っている（IK-0645 と同じ根 — 出所の印が経路を見ない）ため、原文の $…$ を含む抜粋にも印が付く。確定には、PINN の TeX 版のチャンクの formulas[].reconstructed の値と、formula_facts の行の組み立てを読むことが要る。
generalization:
  level: repo_pattern
  general_form: "確かめられ方の印を個々の対象ではなくそれを含む塊に付け、印の付くべきでない対象まで同じ扱いにする"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection, reproduction]
  note: >-
    製品頭脳 A の 4056・B の 4055 に渡ったプロンプト（uxsim/runs/mailbox/req-04055.json・req-04056.json）で、6〜7 件の出典の抜粋のほぼすべてに同じ「（この箇所の式: 番号のない式［AI復元］。…）」が付き、そのうちの一つは原文の $\mathcal{N}_\mathrm{EOS}…$ を含む TeX 版の抜粋だった。学生 st-07 の回答（uxsim/runs/c-astro-r25-share-r5/20261027T071522Z-64789 seq11）は「入出力の細かい形を示した式は AI が文脈から復元したもので原文とは照合できていない」と言った。
resolution:
  perspective: [representation_change]
  note: >-
    core/formula_facts.formula_fact_line を式単位にした。番号の無い復元式は式本文の先頭（論文層の equation_latex_snippet）か定める記号で 1 つずつ名指し、印の判定は equation_latex_is_reconstructed（TeX 原文と一致すれば印なし）だけを使う。復元式と印の無い式が同じ箇所にあれば「印の無い式は原文の数式です」を添え、印の説明は行ごとに繰り返さず RECONSTRUCTED_FORMULA_INSTRUCTION に 1 回だけ置いた。仮説のうち、TeX 経路のチャンクの formulas に reconstructed が立っているか（IK-0645 の根）は砂場の DB で見ていない — 平坦形の source_latex / raw_text との一致で印を外す判定は第 4 波（IK-0977）で入っている。
  landed_in:
    - "backend/core/formula_facts.py"
    - "backend/tests/test_round25_wave5_chat_rag.py"
    - "backend/tests/test_wave17_topic_source_grounding.py"
    - "docs/backend/rag-chat.md"
  verification:
    methods: [guardrail]
    unverified:
      - "PINN の TeX 版のチャンクの formulas[].reconstructed と source_latex の実値（砂場 DB）"
      - "砂場での再演で回答が原文の式を「照合できていない」と言わないこと"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "印の判定は equation_latex_is_reconstructed の 1 つ（annotate_reconstructed_formulas 経由）を通し、事実行の側で独自の判定を持たない。固有の差分: 事実行は式を名指すだけで、印の付け方は変えない。"
related: [IK-0645, IK-0977, IK-0895, IK-0665]
view_of: []
history: []
---

## 課題

症状: 出典の抜粋ごとに同じ［AI復元］の注記が繰り返され、どの式の話かが分からない。TeX 経路の原文の式も復元扱いになり、回答が原文の式を「照合できていない」と言う。警告が氾濫して読まれなくなる。原因（仮説）: 式の事実が抜粋単位・名指しなしで付き、TeX 経路の式に復元の印が立っている。

## 発見の観点

データの点検（製品頭脳に渡ったプロンプト）と再現（st-07 (d) seq11）。製品頭脳 A・B が同じ所見を独立に記した。

## 解決の観点

第 25 周 第 5 波で解決（frontmatter の resolution）。当初の是正案: ① 式の事実は抜粋の本文に実際に現れる式（formulas の latex が抜粋に含まれるか）だけに付け、式を名指す（印字番号か LaTeX の先頭）。② TeX 経路の取り込みで原文の式に reconstructed を立てない（IK-0645 の是正 — 出所の印を経路ごとに）。③ 同じ注記の説明文（「［AI復元］= …」）はプロンプトに 1 回だけ置く。

## 一般化

印を対象でなく塊に付ける型。確かめ方は、TeX 経路の論文で式を含む出典の抜粋に［AI復元］が付かず、PDF 経路の復元式を含む抜粋にだけ式を名指して付くことを見ること。
