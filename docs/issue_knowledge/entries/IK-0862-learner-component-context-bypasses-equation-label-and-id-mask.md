---
id: IK-0862
title: "学生の部品の文脈（⚓ のポップオーバー）が式の表示名と内部 ID の遮断の正本を通らず、式を 80 字で切った生の TeX で、グラフの要約を TeX 経路の式 ID（eq_eosnet・途中で切れた eq_total_los）入りで、注意を方針語（semantic_hint_only）入りで出す"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/display_projection_design.md"
  - "docs/features/component_evidence_redesign.md"
feature_context:
  realizing: "学生が教材の ⚓ から部品を開き、その部品が論文の中で何をしているかを読む"
  layers: [frontend_learning_ui, shared_infra, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 aggregation: 式の表示名の正本は graph_paper_layer.schema.equation_display_label（印字番号 → $…$ の先頭 →
    定義する記号）だが、backend/core/component_context.py:223-233 _equation_label は独自に reconstruction / source_extraction の
    plain_text・latex を [:80] で切って返し（無ければ equation_id そのもの）、:363 の supports.equations[].label になる。
    app.js:8448-8452 は resolver で式が解けないとき escHtml(eq.label) を出すので、生の TeX の途中切れが画面に出る
    （審判 B が learning.component.context / shared_part の両方で検出）。graph_summary_excerpt は :917 で [:200] に切ってから
    投影へ渡し、表示投影の INTERNAL_ID_TOKEN_RE（core/display_projection.py:111-114 の eq[_-] 規則は「数字を含む」ことを
    前提）が TeX 経路の \label 由来の式 ID（eq_eosnet・eq_total_loss — 数字を含まない）を捕まえない（実測: 両方 False）。
    切った後に投影するので途中切れのトークンも残る（切り詰めの順序そのものは IK-0860 が扱う。本件は投影の規則が数字の無い ID を通す側）。接続軸 contract: 表示投影は「内部 ID は数字を含む」形を前提に作られ、
    TeX 経路が数字の無い式 ID を作るようになった側の変化に追随していない。注意の「式抽出は semantic_hint_only 扱い」は
    A層の方針語（component_refiner.py の allowed_downstream_use）が LLM の生成文に写ったもので、投影に方針語の遮断が無い
    （生成箇所は未特定 — 部品組み立ての入力に方針語が載っているかを見れば確定）。論文内の役割が英語の定型文
    （enrichment.py:289）なのは IK-0571 の再発で、本件には含めない。
generalization:
  level: repo_pattern
  general_form: "表示用の値を作る経路の一つが正本の整形関数を通らず自前で切り詰め、遮断の規則も入力の形の前提が変わったことに追随しないため、内部の識別子と切れた記法がその経路からだけ出る"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    学生 3 人が部品の文脈で「eq_eosnet」「（本文を特定できない要素）」「semantic_hint_only」を読めないと記し、審判 B が式の
    表示名の生 TeX を検出した。DTO の各欄を作る関数と表示投影の正規表現を実際の値で試した。run:
    uxsim/runs/c-astro-r25-share-r1/20261022T123418Z-31803 seq7-8・20261022T124951Z-39393 seq7-8・20261022T125633Z-43511 seq7-8。
resolution:
  perspective: [canonical_source]
  note: >-
    component_context._equation_label を graph_paper_layer.schema.equation_display_label へ委譲し、記録の無い式は内部 ID ではなく一般ラベルにした。表示投影 INTERNAL_ID_TOKEN_RE に TeX の \label 由来の数字の無い式 ID（eq_ + 英字始まりの識別子）を足し、A層の写し internal_id_text も同じにした。解析層の方針語（semantic_hint_only 等）を含む注意・入出力の行は学習者に出さない（display_projection.contains_pipeline_policy_token）。グラフ要約の切り詰め順序は IK-0860（面 B）が扱う。
  landed_in:
    - "backend/core/component_context.py"
    - "backend/core/display_projection.py"
    - "src/episteme_graph/agents/internal_id_text.py"
    - "backend/tests/test_component_context_core.py"
    - "backend/tests/test_share_round25_fixes.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "式の表示名を正本の関数に委譲し、遮断の規則を全学習者ルートが通る投影に足した。"
  verification:
    methods: [guardrail]
    unverified:
      - "数字の無い式 ID の遮断が他の学習者向けの面で過剰に効かないか（eq_ で始まる正当な語）は全面の走査をしていない"
      - "graph_summary_excerpt の切り詰め順序（IK-0860）"
related: [IK-0571, IK-0681, IK-0860]
view_of: []
history: []
---

## 課題

症状: 部品の文脈の数式欄に、80 字で切った生の TeX が出る。グラフの要約に TeX 経路の式 ID（eq_eosnet）と途中で切れた ID
（eq_total_los）が混ざり、注意欄に方針語が出る。原因: 部品の文脈が式の表示名の正本を通らずに自前で切り詰め、表示投影の
内部 ID 規則が「数字を含む ID」しか捕まえない。

## 発見の観点

ペルソナの通し受講と審判 B の検出（reproduction）。DTO の組み立てと投影の規則を実際の値で辿った（trace_walk）。

## 解決の観点

未解決。式の表示名は正本 1 つへ委譲し、遮断は投影の 1 箇所で「記録にある ID」を基準にする（形の前提に依らない）。

## 一般化

入力経路（PDF → TeX）が増えると識別子の形が変わり、形で判定する遮断が黙って外れる。確かめ方は、TeX 経路の教材の部品で
文脈 DTO を全欄走査し、記録にある式 ID・方針語・切れた LaTeX が 1 つも残らないことを見ること。
