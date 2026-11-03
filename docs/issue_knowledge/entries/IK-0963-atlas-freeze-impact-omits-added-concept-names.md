---
id: IK-0963
title: "凍結の影響が「次版で加わる関係」は名前で列挙するのに、同じ下書きに取り込んだ概念（項目）は名前で出さず「追加した項目に既存の論文が並ぶのは…」とだけ言う — 何が加わるかを凍結の前に 1 画面で確かめられない"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_binding_lifecycle_design.md"
  - "docs/features/category_gap_candidates_design.md"
feature_context:
  realizing: "教員が論文由来の候補を下書きに取り込んだ後、凍結の影響で次版に何が加わるかを確かめて公開する"
  layers: [field_atlas_s, category_gaps, frontend_admin_ui]
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
    接続軸 information: core/atlas_lifecycle.py compute_freeze_impact は added_node_ids を計算して返し、
    _freeze_impact_facts は関係の増減だけを FACT_ADDED_EDGES_TEMPLATE で名前に写す。追加された項目は
    FACT_ADDED_NODES_NEED_REANALYSIS の一般文だけで名前が無く、admin.js の公開前チェックも added_node_ids を
    描かない（facts と removed の数だけを読む）。計算済みの情報が確定の手前の画面に届かない。統制軸 review: 凍結は
    人の確定操作で、確定の前に提示すべき変更の一覧が欠ける。処理軸・構造軸 none。
generalization:
  level: repo_pattern
  general_form: "確定前の影響の一覧が、変更の種類の一方だけを名前で列挙し、もう一方は計算済みでも一般文で済ませる"
pattern: last-mile-missing
discovery:
  perspective: [reproduction, trace_walk]
  note: "アシスタント頼みの教員が星間磁場を下書きに取り込んだ後に凍結の影響を開き、『次版で加わる関係』は出るのに取り込んだ項目が名前で出ないと記した。compute_freeze_impact の戻りと facts を突き合わせた。run: uxsim/runs/c-astro-r25-atlas-r4/20261024T233234Z-25818 seq 13 / 17。"
resolution:
  perspective: [carry_through]
  note: >-
    core/atlas_lifecycle.added_node_labels が加わる項目を骨格の順に「名前（親領域）」「名前（領域）」へ写し、_freeze_impact_facts が FACT_ADDED_NODES_TEMPLATE「次版で加わる項目: …」を関係と同じ並びで出す（件数なし・初回凍結は全項目になるので出さない）。応答にも added_nodes を足した。admin.js は既存の impact.facts をそのまま描く。
  landed_in:
    - "backend/core/atlas_lifecycle.py"
    - "backend/tests/test_atlas_review_round25_r4.py"
  verification:
    methods: [guardrail]
    unverified:
      - "凍結モーダルでの描画は静的検査のみ"
related: [IK-0885, IK-0886]
view_of: []
history: []
---

## 課題

症状: 凍結の影響に「次版で加わる関係: 惑星形成 — 星形成（関連）」は出るが、同じ下書きに取り込んだ概念「星間磁場」は名前で
出ず「追加した項目に既存の論文が並ぶのは、その論文をもう一度解析したときです。」とだけ言う。

原因: 影響の計算は追加項目の ID を持つが、事実文が関係だけを名前に写す。

## 発見の観点

通し受講（reproduction）で教員が記し、影響の計算と事実文の組み立てを辿った（trace_walk）。

## 解決の観点

第 4 波（2026-10-25）で解決。core/atlas_lifecycle.added_node_labels が加わる項目を骨格の順に「名前（親領域）」「名前（領域）」へ写し、_freeze_impact_facts が FACT_ADDED_NODES_TEMPLATE「次版で加わる項目: …」を関係と同じ並びで出す（件数なし・初回凍結は全項目になるので出さない）。

## 一般化

影響の一覧は、変更の種類ごとに名前で列挙されているかを揃える。確かめ方: 項目と関係を 1 つずつ足した下書きで
凍結の影響を開き、両方の名前が出ることを見る。

第 5 周の再演（2026-10-27・campaign c-astro-r25-atlas-r5）: 解消を確認。凍結の影響が「次版で加わる項目: 星間磁場（星間物質・星形成）」を「次版で加わる関係: ブラックホール — 重力波（関連）」と同じ並びで名前で出し、そのまま凍結が成立した。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T070623Z-23509 seq 13 / 14 / 15。
