---
id: IK-0966
title: "「候補の条件から外れた採用済みの判断」の区画の冒頭文が、行を下書きへ取り込んだ後も「取り込むか、候補へ戻すまで公開（凍結）はできません」と言い続け、もう止めていない行を止めていると読ませる"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/category_gap_candidates_design.md"
feature_context:
  realizing: "教員が外れた採用済みの候補を下書きへ取り込み、凍結を止めるものが残っているかを候補の区画で確かめる"
  layers: [category_gaps, field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 condition: core/atlas_review_display.py ORPHANED_NOTE は区画の見出しの説明として条件なしで出る。
    行ごとには ORPHANED_IN_DRAFT（次版の下書きに入っています）が付くが、区画の文は行の状態を見ない。全行が
    取り込み済みでも「公開（凍結）はできません」と断言する。処理軸 wording。IK-0943（区画の説明が空と矛盾）と
    同じ形の残り。
generalization:
  level: repo_pattern
  general_form: "区画の冒頭の断言が区画内の行の状態を見ず、行がすべて条件を満たした後も条件を満たしていないと言う"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "教員が星間磁場を取り込み、行には『次版の下書きに入っています』と出たのに上の文は『取り込むまで公開できません』のままで、止まっていないか不安と記した。run: uxsim/runs/c-astro-r25-atlas-r4/20261024T233234Z-25818 seq 8 / 14。"
resolution:
  perspective: [single_point_fix]
  note: >-
    区画の冒頭を定義の文にし（ORPHANED_NOTE / EDGE_ORPHANED_NOTE）、凍結を止める一文（ORPHANED_BLOCKS_FREEZE / EDGE_ORPHANED_BLOCKS_FREEZE）は下書きに入っていない行があるときだけ atlas_review_display.orphaned_note が足す。候補・辺の両ルートが使う。
  landed_in:
    - "backend/core/atlas_review_display.py"
    - "backend/api/routes/atlas_gaps.py"
    - "backend/api/routes/atlas_edges.py"
    - "backend/tests/test_atlas_review_round25_r4.py"
  verification:
    methods: [guardrail]
    unverified: []
related: [IK-0943, IK-0937]
view_of: []
history: []
---

## 課題

症状: 外れた採用済みの判断の区画で、行を取り込むと行には「次版の下書きに入っています」が出るが、区画の冒頭は
「次版の下書きへ取り込むか、候補へ戻すまで公開（凍結）はできません」のまま。

原因: 区画の文が行の状態を見ない断言。

## 発見の観点

通し受講（reproduction）で教員が記した。

## 解決の観点

第 4 波（2026-10-25）で解決。区画の冒頭を定義の文にし（ORPHANED_NOTE / EDGE_ORPHANED_NOTE）、凍結を止める一文（ORPHANED_BLOCKS_FREEZE / EDGE_ORPHANED_BLOCKS_FREEZE）は下書きに入っていない行があるときだけ atlas_review_display.orphaned_note が足す。

## 一般化

区画の見出しの説明は、区画の中身が全部「済み」になっても正しいかを確かめる。確かめ方: 区画の行を全部取り込んだ状態で
文を読む。

第 5 周の再演（2026-10-27・campaign c-astro-r25-atlas-r5）: 解消を確認。星間磁場を取り込んだ後、区画の冒頭から「公開（凍結）はできません」が消え定義の文だけになった。凍結の後は区画ごと消える。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T070623Z-23509 seq 7 / 8、uxsim/runs/c-astro-r25-atlas-r5/20261027T071308Z-52116 seq 8。
