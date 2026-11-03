---
id: IK-0849
title: "関係（辺）の共起候補は支持論文の題名しか出さず、両端の配置が持つ理由と逐語の根拠（landscape_placements.reason / evidence）を運ばないため、教員は題名の推測でしか「論文がその関係を言っているか」を判断できない（カテゴリギャップ候補は逐語つきで非対称）"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
feature_context:
  realizing: "教員が関係（辺）の候補の採否を、候補を支える論文の本文に照らして決める"
  layers: [relation_edges_re, knowledge_landscape, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [review]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: 共起候補の材料は landscape_placements の live 行で、各行は reason と evidence（逐語引用の JSONB。
    backend/db/065_*.sql の reason / evidence 列）を持つが、atlas_edges/derive.py:168-190 _fetch_placements は
    document_id・題名・node_id だけを SELECT し、derive_co_occurrence_pairs の documents は {document_id, title} のみ。
    admin.js:7708 _edgeDocumentsHtml も題名だけを描く。カテゴリギャップ候補は理由と逐語を描く（_gapDocumentsHtml）ので、
    同じ区画の 2 種類の候補で根拠の見え方が違う。統制軸 review: 判断に要る根拠が判断の画面に無く、採否が題名の推測になる
    （教員ペルソナ「題名の推測でしか採否を決められない」）。処理軸・構造軸 none。
generalization:
  level: repo_pattern
  general_form: "候補を導出した材料の行が根拠（理由・逐語）を持っているのに、導出の段が識別子と題名だけを後段へ渡し、判断の画面で根拠が読めない"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, trace_walk]
  note: "教員ペルソナが辺候補に逐語が無く gap 候補と非対称だと記した。共起候補の導出 SQL と配置表の列を突き合わせ、根拠が材料には在って導出で落ちることを確かめた。 run: uxsim/runs/c-astro-r25-atlas-r1/20261022T123418Z-31802（頭脳の所見 uxsim/runs/round25/BRAIN_NOTES_atlas.md）。"
resolution:
  perspective: [carry_through]
  note: >-
    atlas_edges/derive.py の _fetch_placements が配置の reason と evidence を SELECT し（weight / confidence は SELECT しない）、derive_co_occurrence_pairs が documents の各行に両端の endpoints（node_label・reason・先頭の逐語）を載せる（重複取り込みのどちらかの配置から採る）。admin.js の _edgeDocumentsHtml は gap と同じ details 形で描く。
  landed_in:
    - "backend/core/atlas_edges/derive.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_atlas_review_round25.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "根拠の見せ方を gap と同じ形（題名 → 理由と逐語の details）に揃えた。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の配置に reason / evidence が実際に入っているかは再演で未確認"
related: [IK-0846]
view_of: []
history: []
---

## 課題

症状: 辺候補（共起）の「両方の項目に配置がある論文」は題名だけで、関係を論文がどう言っているかを確かめる逐語が無い。
同じ区画のカテゴリギャップ候補は理由と逐語を出す。原因: 共起の導出が配置行の理由・逐語を SELECT せず、後段に渡さない。

## 発見の観点

ペルソナの通し受講で教員が根拠の非対称を記し（reproduction）、導出の SQL を辿った（trace_walk）。

## 解決の観点

辺の共起候補に両端の配置の理由と逐語を運び、gap と同じ形で描く。

## 一般化

候補の導出で根拠を落とすと、人の確定が根拠に照らした判断ではなく題名・名前の推測になる。確かめ方は、辺候補の各支持論文に
両端の逐語が出て、その逐語が配置の evidence と一致することを見ること。

## 第 2 周の再演（2026-10-23・campaign c-astro-r25-atlas-r2）

- 未観測。IK-0846 の是正で共起由来の辺候補が立たなくなり（砂場の別々の論文の配置では共起の閾値を満たさない）、表示された
  候補はすべてベクトル近傍由来だった。ベクトル近傍由来の候補に根拠が付かない・全件同じ段階ラベルになる件は IK-0887 に分けた。

## 第 7 周の再演（第 25 周・面 C 第 7 周、2026-10-28・campaign c-astro-r25-atlas-r7）

未観測。共起由来の候補は 7 周目も立たない（材料）。
