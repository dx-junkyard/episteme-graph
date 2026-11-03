---
id: IK-0679
title: "「次へ」の確定文脈の提示集合が押した時点の未確認だけで組まれ、同じ画面で根拠を開いてから却下・再検討した行が「根拠を開いた行」として提示集合の外に出る"
status: open
recorded_at: 2026-10-09
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/decision_context_design.md"
  - "docs/features/release_review_flow_design.md"
feature_context:
  realizing: "教員がリリース前の確認で根拠を読み、一部を却下・再検討してから「次へ」を押し、後から何を見て選んだかを再構成する"
  layers: [decision_context, release_review]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [review]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: admin-release-review.js は presented_placement_ids を status=inferred の行（pendingPlacementIds）で組み、evidence_expanded_placement_ids は画面で開いた全行（却下・再検討した行を含む）を送る。審判 E6 が「根拠を開いた行が提示集合の外」と出した（te-03 run 32999）。行番号のずれ（ハーネス）は別の偽所見だが、正しく操作しても同じ不整合が起きる。構造軸 representation = 「提示された」の単位（画面に出た行 / 「次へ」の対象）が区別されていない。
generalization:
  level: repo_pattern
  general_form: "記録の中の 2 つの集合が別々の単位で組まれ、片方がもう片方の部分集合であるはずの関係が崩れる"
pattern: unit-of-work-undefined
discovery:
  perspective: [reproduction]
  note: "第 23 周の審判 E6（確定文脈）が、几帳面な教員の運転で提示と開いた根拠の集合の不整合を出した。"
resolution:
  perspective: [pending]
  note: "未解決（第 3 波で一部是正: 提示集合を閲覧できる全論文の inferred に広げた — IK-0688）。残り: ステップ 2 で個別に判断した行が提示集合に入らない。推奨（core + frontend）: 提示集合を「画面に出ていた行」で組み、適用集合（inferred → confirmed）と個別判断の行を別キーで残す（DC2 の導出は不変）。"
related: [IK-0659, IK-0674]
view_of: []
history: []
---

## 課題

症状: 確定の記帳で、開いた根拠の行が提示集合に含まれない。原因: 提示の単位が「次へ」の対象と同じになっている。

## 発見の観点

審判 E6 の所見（reproduction）。

## 解決の観点

未解決。提示・個別判断・適用の 3 つを別の集合として記帳する。

追記（2026-10-10・第 3 波の後）: 提示集合は「閲覧できる全ソース論文の live な inferred」に広がった（IK-0688）が、ステップ 2 で個別に却下・再検討した行は「次へ」の時点で inferred ではないため提示集合に入らず、根拠を開いた行の申告がそれらを含むと提示集合の外に出る関係は残る。個別判断した行を提示の記録の別キーに残すまでは未解決。

## 一般化

記録の集合を別々の単位で組むと、後から再構成するときに含まれるはずの関係が崩れる。確かめ方は、個別に判断した行を含む操作列で記帳し、集合の包含関係を検査すること。
