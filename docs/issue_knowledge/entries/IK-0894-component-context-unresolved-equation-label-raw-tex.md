---
id: IK-0894
title: '学生の部品の文脈の「数式」で、教材の式に解決できない式は表示名をそのまま文字として出すため、$…$ を含む表示名（「番号なし: $\mathbf{F}_{\!\mathrm{EOS}} = \{ P(k)$…」）が生の TeX のまま描かれ、TeX のラベル由来の「式 (eq:tovnet)」と並ぶ'
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が教材の ⚓ から部品の文脈を開き、部品が使う式を読む"
  layers: [frontend_learning_ui, learner_experience_b, graph_paper_layer]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
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
    接続軸 contract: 式の表示名の正本 backend/core/graph_paper_layer/schema.py:326 equation_display_label は、印字番号が無いとき「番号なし: $…$」（数式区間を含む文字列）を返し、数式として描くことを受け手に求める。component_context._equation_label（backend/core/component_context.py:241）は IK-0862 の是正でこれへ委譲した。一方、画面 frontend/public/js/app.js:8510-8519 renderContextEquationSection は、教材の式に解決できたときだけ renderMaterialKatex で描き、解決できない式は escHtml(eq.label) で文字として出す。表示名の契約（$…$ は数式）を描画側が受けていない。部品の文脈を旅で隣の部品へ移ると、その部品の式は表示中トピックの式に無いことが多く、この分岐に落ちる。TeX 経路の印字番号の無さ（式 (eq:tovnet)）は IK-0681。構造軸は none、統制軸は none。
generalization:
  level: repo_pattern
  general_form: "数式区間を含む表示名を返す正本に委譲した後、受け手の描画が一部の分岐で文字として出し、委譲の効果がその分岐だけ消える"
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    学生 st-06 が部品の文脈から隣の部品（EOS feature vector）へ移ると、数式欄が「$\mathbf{F}_{\!\mathrm{EOS}} = \{ P(k)$…」と生の TeX で出た（ハーネスは画面と同じく解決できない式の label を出す）。表示名の組み立てと画面の分岐を突き合わせた。run: uxsim/runs/c-astro-r25-share-r2/20261022T220149Z-75546 seq19。
resolution:
  perspective: [explicit_contract]
  note: >-
    component_context の equations に式本文 latex（graph_paper_layer.schema.equation_display_latex = 区切りを外し原子の境目で切った LaTeX）を同梱し、app.js renderContextEquationSection は教材の式に解決できない分岐でもそれを数式で描く。latex が無いときは表示名の $…$ を数式区間として描く（renderTextWithInlineMath）。
  landed_in:
    - "backend/core/component_context.py"
    - "backend/core/graph_paper_layer/schema.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_share_round25_r2_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面での旅の先の部品の式の見え方"
related: [IK-0862, IK-0681]
view_of: []
history: []
---

## 課題

症状: 部品の文脈の数式欄が、生の TeX の断片（$…$ と途中の … 付き）で出る。原因: 表示名の正本は数式区間を含む文字列を返すのに、
画面は教材の式に解決できない式の表示名を文字として描く。

## 発見の観点

学生ペルソナの通し受講（reproduction）。表示名の組み立てと描画の分岐を辿った（trace_walk）。

## 解決の観点

第 2 波（2026-10-23）で是正。表示名の契約（$…$ は数式）を全分岐で受け、式本文も DTO に載せる。

## 一般化

正本へ委譲しても、受け手の一部の分岐が契約を受けていない型。確かめ方は、表示中トピックの式に無い式を持つ部品へ旅で移り、
数式欄が数式として描かれ、生の TeX の記号が文字として残らないことを見ること。
