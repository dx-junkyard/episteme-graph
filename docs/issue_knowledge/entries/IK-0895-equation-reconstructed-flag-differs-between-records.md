---
id: IK-0895
title: "TeX 経路の教材で、同じ式が教材の「この節で使う数式」では原文の式として出るのに、部品の文脈では「AI が文脈から復元した式です（原文の数式とは未照合）」と添えられる — 同じ式の表示式ブロックと本文中の式の候補が別々の式の記録を持ち、復元の印をそれぞれの記録の reconstruction.status で付けるため、画面ごとに式の確かめられ方が食い違う"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "学生が教材と部品の文脈で同じ式を読み、それが原文の式か AI の復元かを確かめる"
  layers: [learner_experience_b, pipeline_a, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
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
    仮説: 部品 EOS feature vector の式は eq_eqcand_inline_tex_b14_383_…（本文中の式の候補）で、learner_context_common.equation_latex_is_reconstructed（backend/core/learner_context_common.py:398）は reconstruction.status が none 以外で復元本文があれば復元と判定する。教材の数式一覧は chunks.formulas[].reconstructed（course_content_builder._equation_is_reconstructed :4116 と同じ規則）を使うが、表示されている式は別の記録（表示式ブロック）由来で reconstructed が付かない。構造軸 representation: 同じ数式が式ブロックと本文中の候補の 2 つの記録になり、どちらが正本かを持たない。接続軸 meaning: TeX 経路では原文に LaTeX があるので、equation_semantics が reconstruction を付けても「原文と未照合」とは限らないが、判定は取り込み形式を見ない（IK-0870 の但し書きと同じ形）。確定には採用 run の equation_semantics artifact で eq_eqcand_inline_tex_b14_383_… と同じ式の表示式ブロックの記録の reconstruction.status を比べ、TeX 経路の inline 候補で status が付く条件を読めばよい。
generalization:
  level: repo_pattern
  general_form: "同じ対象が抽出経路の違いで 2 つの記録になり、属性（真正性の印）を記録ごとに判定するため、画面ごとに印が付いたり付かなかったりする"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    学生 st-06 が隣の部品へ移ると、教材では通常表示の F_EOS の式に部品の文脈では「AI が文脈から復元した式です」が付いていた。同じコースの別の版（6be229e1）でも「式 (4)」「式 (2)」が両方復元扱いで中身が無かった（st-09）。run: uxsim/runs/c-astro-r25-share-r2/20261022T220149Z-75546 seq19・20261022T220528Z-78210 seq7。
resolution:
  perspective: [canonical_source]
  note: >-
    復元の印の判定の正本を learner_context_common.equation_latex_is_reconstructed に 1 つ置き、復元本文が原文の LaTeX（source_extraction.latex）と正規化後（空白・間隔命令・\left/\right・\label・末尾の句読点を除く）に一致するなら印を付けない（reconstruction_matches_source）。course_content_builder._equation_is_reconstructed は委譲し、パイプラインの chunks.formulas の reconstructed も同じ判定を使う（新しい run から）。式ブロックと inline 候補の読み時の同一視（是正案②）は見送った。
  landed_in:
    - "backend/core/learner_context_common.py"
    - "backend/core/course_content_builder.py"
    - "backend/core/document_pipeline/persistence.py"
    - "backend/tests/test_share_round25_r2_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "既存の chunks.formulas の reconstructed は再解析まで旧い判定のまま（教材の式一覧は再解析で揃う）"
      - "式ブロックと inline 候補の同一視（是正案②）は未実装"
related: [IK-0870, IK-0681, IK-0443]
view_of: []
history: []
---

## 課題

症状: 同じ式が教材では原文の式、部品の文脈では AI の復元として出る。原因（仮説）: 同じ数式が別々の式の記録になり、復元の印を
記録ごとに、取り込み形式を見ずに付ける。

## 発見の観点

学生ペルソナの通し受講（reproduction）。教材の数式一覧と部品の文脈の式の記録を並べた（data_inspection）。

## 解決の観点

第 2 波（2026-10-23）で是正。復元の判定を 1 つの正本にし、TeX 経路で原文と一致する式には印を付けない。

## 一般化

抽出経路の違いで同じ対象が 2 つの記録になり、属性が食い違う型。確かめ方は、TeX 経路の教材で同じ式を教材・部品の文脈・要素の
文脈の 3 画面で開き、復元の印が一致することを見ること。
