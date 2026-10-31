---
id: IK-0641
title: "台帳候補の文面が実行の比較の文を主張ごとに複製するだけで、その実行が各主張をどの記号でどう確かめたかが書かれず、選べる主張も観測量の定義文に偏る"
status: open
recorded_at: 2026-10-04
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §19.6"
  - "docs/features/simulation_bridge_design.md §12.5"
feature_context:
  realizing: "学習者の共有した実行から台帳候補を主張ごとに記帳し、教員がその候補を読んで確定・見送りを判断する"
  layers: [simulation_bridge_sb, doubt_d]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: 候補の記帳（store の append）は run 単位の statement と facts を選ばれた主張ごとに複製するだけで、主張と比べた記号の対応を持たない。観測系の主張の候補は観測量の定義文が多く、実行が確かめる結果の主張ではない。構造軸 representation = 主張↔記号の対応を置く居場所が候補に無い。接続軸 information = 実行のどの比較がどの主張に当たるかが後段（教員）へ渡らない。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "1 回の結果を複数の対象への根拠として記録するとき、結果のどの部分がどの対象に当たるかの対応を持たず、同じ文面が対象ごとに並ぶ"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 22 周の教員場面 (g') で両教員が候補の違いを読めず、学生場面 (e''-only) でも同じ文面の候補が 2 件並んだ。"
resolution:
  perspective: [pending]
  note: "未解決。主張ごとに比べた記号（と記載値）を候補に持たせ、文面をその対応から組めば解ける。観測量の定義文を候補から外すかは主張の種類の判定が要る。"
related: [IK-0622, IK-0626]
view_of: []
history: []
---

## 課題

症状: 同じ実行から立った候補が主張ごとに同じ文面で、どの主張をどう確かめたかが読めない。原因: 記帳が run 単位の文を複製するだけで、主張と記号の対応を持たない。選べる主張も定義文が多い。

## 発見の観点

ペルソナの通し受講（reproduction）で教員・学生の両方が候補を区別できなかった。

## 解決の観点

未解決。主張↔記号の対応を候補の表現に加える必要がある。

## 一般化

1 つの結果を複数の対象の根拠にすると、対応を持たない限り同じ文面が並ぶ。型は information-dropped-as-unrepresentable。確かめ方は、2 つの主張を選んで共有し、候補ごとに比べた記号が異なって読めることを見ること。
