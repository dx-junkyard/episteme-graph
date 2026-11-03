---
id: IK-0970
title: "前提の地図と未検証合意リストは、導出の手順（claim チェーン由来のノード）を母集合から外したあとも、「依存の広がり」の段階の閾値（p50 / p90 / p99）を外す前の全行で計算しているため、残った主張・ノードが高の段に届かず、4 本の論文を持つ 3 つのコースで地図もリストも空になる（仮説）"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/features/doubt_layer_issues.md"
feature_context:
  realizing: "教員がコースの前提の地図と未検証合意リストで、依存の広がりが大きく検証の記録が無い対象を探す"
  layers: [doubt_d, seminar_brief, frontend_admin_ui]
classification:
  axes:
    processing: [logic]
    structure: [aggregation]
    connection: [none]
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
    仮説: routes/doubt.py get_assumption_atlas と core/doubt/open_assumptions.py は load_percentiles(session, document_ids=…) を範囲の全台帳行で取り、
    その後で ClaimChainExclusion が claim チェーン由来の行を外し、残りを load_level_for_score で段に変換して high / highest だけを載せる。第 3 周に地図を
    埋めていたのは導出の手順の行（IK-0933）で、それが分布の上側を占めるなら、外した後の主張・主グラフのノードは p90 を超えない。処理軸 logic（閾値と判定対象の
    母集団が食い違う）。構造軸 aggregation（集計の母集団の取り方）。確定には、砂場の 4 論文の epistemic_ledger の load_score を、除外する行・残す行に分けて
    p90 と比べる（読み取りの SQL 1 本）。もう一つの可能性（主張・ノードの load_score が NULL）も同じ照会で見分けられる。
generalization:
  level: general
  general_form: "表示の母集団から一部を除外したのに、段階の閾値は除外前の母集団で計算するため、残った項目がどれも上の段に届かない"
pattern: threshold-reused-across-regimes
discovery:
  perspective: [symptom_report, trace_walk]
  note: >-
    第 4 周、te-01・te-03・te-05 の 3 人が別々のコース（6be229e1 / 74ca9b48 / 7dcbf662・いずれも 4 本の論文）で前提の地図を開き、散布図・未検証合意リストとも空、
    「導出の手順は含めていません」の注記だけが出た。第 3 周は同じ母集団が導出の手順で埋まっていた。応答から閾値と除外の順序をコードで辿った。
    学生 st-07 の「このコースの教材で、検証の記録がない前提を見る」も空。run: uxsim/runs/c-r25-doubt-r4/20261024T232812Z-98847（seq3）/
    uxsim/runs/c-r25-doubt-r4/20261024T233017Z-7034（seq3・12）/ uxsim/runs/c-r25-doubt-r4/20261024T233201Z-20444（seq3〜6）/ uxsim/runs/c-r25-doubt-r4/20261024T233257Z-31520（seq11）。
resolution:
  perspective: [canonical_source, carry_through]
  note: >-
    load_percentiles に exclusion（ClaimChainExclusion）を渡すと、行を読んで一覧と同じ除外を掛けた母集団で p50/p90/p99 を取る（percentiles_from_scores は percentile_cont と同じ線形補間の純関数。閾値の計算では excluded の印を立てない）。前提の地図・未検証合意リスト（ゼミ前ブリーフ経由を含む）・台帳詳細の負荷段がこの経路を通る。教員のトリアージ（teacher_triage の sort=load）は未適用。
  landed_in:
    - "backend/core/doubt/load_calculator.py"
    - "backend/core/doubt/open_assumptions.py"
    - "backend/api/routes/doubt.py"
    - "backend/tests/test_doubt_round25_wave4.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "閾値の取り方を load_percentiles の 1 箇所に置き、除外の判定も ClaimChainExclusion の 1 箇所を通す。"
  verification:
    methods: [guardrail]
    unverified:
      - "仮説（導出の手順が分布の上側を占める）は合成データで除外前後の分布を並べて確かめただけで、砂場の 4 論文の epistemic_ledger の実分布（除外する行・残す行の load_score）は照会していない（cause_status は hypothesis のまま）"
      - "主張・ノードの load_score が NULL である可能性の切り分けも未実施"
      - "teacher_triage の母集団は未統一"
related: [IK-0933, IK-0853]
view_of: []
history: []
---

## 課題

症状: 導出の手順を前提の地図から外した後、3 つのコースで地図も未検証合意リストも空になった（主張は依存の広がりが大きいものだけ載る、という空の理由は出る）。

原因（仮説）: 段の閾値を除外前の全行で計算しており、外した導出の手順が分布の上側を占めていたため、残りがどれも高の段に届かない。

## 発見の観点

3 人の教員が別のコースで同じ空を見た（症状の報告）。第 3 周の埋まり方と第 3 波の除外を、閾値の計算の順序まで辿った（経路の追跡）。

## 解決の観点

第 4 波で是正（2026-10-25）。閾値を表示の母集団で取る。

## 一般化

除外・絞り込みを足したら、相対の閾値（百分位）を取る母集団も同じ絞り込みに揃える。確かめ方は、除外前後の分布を並べ、除外後の母集団で各段に項目が入ることを見ること。

第 5 周の再演: 空の一覧は GONE（2026-10-27）。te-02 の自コース・te-04 / te-03 の共有コースとも散布図と未検証合意リストに点・行が並び、学生の未検証の前提一覧（st-10）も空でなくなった。ただし［依存の広がりを再計算］で中身が丸ごと入れ替わる（計算範囲の違い）件を IK-1001 に、段『最高位』と『0 箇所が依存』の食い違いを IK-1002 に起票。run: uxsim/runs/c-r25-doubt-r5/20261027T070623Z-23504（seq3）/ uxsim/runs/c-r25-doubt-r5/20261027T071425Z-60143（seq3・8）/ uxsim/runs/c-r25-doubt-r5/20261027T071839Z-88666（seq11）。
