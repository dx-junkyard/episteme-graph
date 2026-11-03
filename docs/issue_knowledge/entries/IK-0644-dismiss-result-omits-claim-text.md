---
id: IK-0644
title: "台帳候補を見送った結果の事実文に、見送った主張の本文が出ず、どちらの候補を見送ったか結果画面で確かめられない"
status: open
recorded_at: 2026-10-04
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "教員が台帳候補を見送り、その結果を画面で確かめる"
  layers: [simulation_bridge_sb, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 見送りの応答の事実文は見送った事実だけを言い、主張の本文（ラベル）を含まない。候補の文面が主張ごとに同じ（IK-0641）なので、結果からは対象が分からない。処理軸 wording。接続軸 information = 対象の主張が結果の文へ渡らない。構造軸 none。統制軸 none。
generalization:
  level: instance
  general_form: "操作の結果の文が対象を名指さず、同じ見た目の対象が複数あるとどれに効いたか確かめられない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 22 周の教員場面 (g') で、見送った後にどちらの候補を見送ったかを結果から確かめられなかった。"
resolution:
  perspective: [pending]
  note: "未解決（軽微）。見送りの事実文に主張のラベルを入れれば解ける。"
related: [IK-0641]
view_of: []
history: []
---

## 課題

症状: 見送った結果から、どの主張を見送ったか分からない。原因: 結果の事実文が主張を名指さない。

## 発見の観点

ペルソナの通し受講（reproduction）の教員場面で見えた。

## 解決の観点

未解決。事実文に主張のラベルを添える。

## 一般化

結果の文が対象を名指さないと、似た対象の取り違えを確かめられない。型は information-dropped-as-unrepresentable。確かめ方は、似た候補 2 件の片方を見送り、結果の文から対象が特定できることを見ること。
