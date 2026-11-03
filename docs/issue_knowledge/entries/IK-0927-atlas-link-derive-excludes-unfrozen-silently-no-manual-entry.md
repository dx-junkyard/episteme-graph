---
id: IK-0927
title: "ナレッジライブラリの「地図との対応を導出」は、まだ凍結していないエントリをベクトルの照合から黙って外し（凍結版の embedding しか読まない）、「新しい対応の候補はありませんでした」とだけ言い、候補が出ないときに教員が手で対応を付ける入口も無い"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が共通部品が分野の地図のどこに当たるかを記録する"
  layers: [concept_registry, field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [assignment]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 condition: core/library/atlas_links.py:10-11・:177-189 のベクトル規則はエントリの凍結版の
    embedding（library_entry_versions）だけを読み、凍結版の無いエントリ（「まだ凍結していません」）は語彙一致の規則にしか乗らない。derive の facts はこの条件を言わない。統制軸 assignment:
    routes/library.py の atlas-links は GET / decide / derive だけで、教員が node を選んで対応を作る経路が無い — 判断の弁はあるが、候補が来ないときの入口が無い（IK-0116
    と同じ形）。
generalization:
  level: repo_pattern
  general_form: "候補の供給が隠れた条件で止まり、候補が来ないときに人が直接記録する入口も無い"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員 te-02 が「TOV 方程式」エントリで導出し、候補ゼロの理由が無く手で付ける出口も無いと記して止まった（blocked）。骨格には「中性子星」の概念がある（backend/atlas_domains/astrophysics/sk
    eleton.yaml）。run: uxsim/runs/c-astro-r25-share-r3/20261023T033832Z-66905 seq7-8。
resolution:
  perspective: [first_class_state]
  note: >-
    core/library/atlas_links.py の導出が、ベクトル索引があるのに凍結版の無いエントリがあるとき FACT_UNFROZEN_ENTRIES_LEXICAL_ONLY を、候補ゼロのとき FACT_MANUAL_LINK_ENTRY を事実文に足す。手動の入口として GET /api/admin/library/atlas-links/nodes（現行凍結骨格の概念を名前で検索・search_concept_nodes）と POST /api/admin/library/entries/{id}/atlas-links（manual_curation の候補・現行版に無い node は 422・骨格に書かない）を足し、admin.js のエントリ詳細「分野の地図との対応」区画に検索欄・選択・［対応を付ける］（アンカー knowledge-library.atlas-link-manual・マニュアル節 #atlas-link-manual）。確定は既存の一覧の［確定］。
  landed_in:
    - "backend/core/library/atlas_links.py"
    - "backend/api/routes/library.py"
    - "frontend/public/js/admin.js"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "docs/manual/teacher/19-admin-knowledge-library.md"
    - "docs/admin_operations/library.md"
    - "backend/tests/test_share_round25_r3_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面での手動の対応付け（次周の te-04 × (c)）"
      - "教員の手動作成を即確定にするかは決めていない（候補で入り既存の確定で確定する）"
related: [IK-0116, IK-0864]
view_of: []
history: []
---

## 課題

症状: 導出しても候補ゼロで、理由が分からず、手で対応を付けられない。原因: ベクトル照合が凍結版だけを読み、その条件を言わず、手動の入口が無い。

## 発見の観点

教員ペルソナの通し受講（reproduction）。導出の規則を辿った（trace_walk）。

## 解決の観点

是正済み（第 3 波）。導出の照合条件と手動の入口を事実文で言い、地図の概念を名前で探して手で対応の候補を付ける入口を足した。

## 一般化

候補頼みで入口が無い型（IK-0116 と同族）。確かめ方は、凍結していないエントリで導出して条件の事実文が出ること、候補ゼロでも手動で対応を 1 つ作り確定できることを画面で見ること。
