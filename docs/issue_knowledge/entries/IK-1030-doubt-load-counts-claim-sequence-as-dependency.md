---
id: IK-1030
title: "依存の広がり（load_score）の計算が、論文の主張を出現順に並べた claim チェーンの辺を「この主張が偽なら下流が崩れる」依存として数えるため、論文 1 本の範囲で再計算すると序論の背景文（Stars predominantly form within filamentary structures… など）ほど下流到達が大きく「最高位」になり、前提の地図・未検証合意リスト・学生の「検証の記録がない前提」・discuss 開幕の「この論文が確かめていないこと」が序論の一般論で埋まる"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "依存の広い・検証の薄い前提を、教員と学生が前提の地図と一覧から選べるように段階で並べる"
  layers: [doubt_d, pipeline_a, discuss]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: low
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: core/doubt/dependency.py の build_dependency_graph は graph_layer が debug / inferred 以外の全ノードと辺を使い、IK-0933 の是正は
    「依存の伝播（負荷）は従来どおり全ノードで計算する」として claim チェーン（主張の出現順の並び）の辺を残した。主張の台帳行の seed は
    required_claim_ids で claim チェーンのノードに当たり、出現順の辺を下流へ辿るので、先頭に近い序論の主張ほど到達集合が大きい。
    run: te-04 の［負荷再計算］（論文単位・IK-1001 の是正後）の直後、散布図と合意リストの先頭が式（dependent_count 18 等）から Cepheus の
    序論の英語の主張（load 段「最高位」・dependent_count 0）に入れ替わり、同じ再計算の後に学生の一覧と開幕も同じ文で埋まった。
    再計算前の値は第 5 波より前の範囲で計算された古い値だった可能性があり（入れ替わり自体は IK-1001 の残り）、序論が上位に来る理由は
    出現順の辺。確定には、Cepheus の 1 論文で claim チェーンの辺を除いた依存グラフと除かない依存グラフで同じ台帳行の load_score を並べる
    読み取り（compare_load_scopes と同型）を 1 本走らせる。処理軸 logic = 「並び」を「依存」として数える判定。接続軸 meaning = 出現順の辺の
    意味（読む順）が負荷計算で別の意味（崩れの伝播）として読まれる。
generalization:
  level: general
  general_form: "表示の順序を表すために張った辺を、依存の伝播を計算するグラフにも含めると、並びの先頭にあるものほど重要と判定される"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection, trace_walk]
  note: "te-04 の［負荷再計算］前後の assumption-atlas / open-assumptions の生応答（target_type・load_level・dependent_count）を並べ、直後の st-05 の学習者の一覧・開幕と照らし、dependency.py と IK-0933 の是正記録を読んだ。run: uxsim/runs/c-r25-doubt-r6/20261027T122831Z-7319（seq3・7）/ uxsim/runs/c-r25-doubt-r6/20261027T123134Z-9179（seq11・15）。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    負荷（recompute_load_scores・compare_load_scopes）と反実仮想（compute_counterfactual）の依存グラフを build_dependency_graph(exclude_claim_sequence_edges=True) で作り、claim チェーン由来ノードに接する辺を依存に数えない（オーナー判断・推奨どおり）。判定は一覧の母集合（IK-0933）と同じ正本 claim_sequence_node_ids を population.claim_sequence_ids_for_payload 経由で通す（D層で再判定しない）。ノード・参照は残し、反実仮想の伝播規則は不変（辺の母集合だけ）。仮説の読み取り compare_scores_with_and_without（純関数）/ compare_claim_sequence_edges（1 論文・書き込みなし）を足し、テストで「出現順の先頭の主張だけが縮む」を固定した。
  landed_in:
    - "backend/core/doubt/dependency.py"
    - "backend/core/doubt/population.py"
    - "backend/core/doubt/load_calculator.py"
    - "backend/core/doubt/counterfactual.py"
    - "backend/tests/test_doubt_round25_wave6.py"
    - "docs/features/stakes_ledger_design.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "出現順の辺かどうかの判定を負荷・反実仮想で別に持たず、一覧の母集合と同じ claim_sequence_node_ids 1 箇所を通した。固有の差分: ここは表示の母集合ではなく計算の辺の母集合で、ノードと参照は残す。"
  verification:
    methods: [guardrail]
    unverified:
      - "実データ（Cepheus TeX）で compare_claim_sequence_edges を走らせて序論の主張が縮むことは確かめていない（砂場 DB に触れない是正者のため）"
      - "是正前の load_score が台帳に残っているので、全論文の再計算（POST /api/admin/doubt/courses/{id}/load/recompute）を運転側で行うまで一覧は変わらない"
      - "支持線・ゼミ前ブリーフ・反証条件の入力は既定（辺を外さない）のまま"
related: [IK-0933, IK-1001, IK-1002, IK-0730]
view_of: []
history: []
---

## 課題

症状: te-04 が自分のコースで［負荷再計算］を押すと「再計算しました」の後、散布図と合意リストの中身が式の並びから Cepheus の序論の英語文に丸ごと入れ替わり、
序論の一般論が依存の広がり「最高位」、隣の欄は「0 箇所が依存」。同じ後に学生 st-05 の「このコースの教材で、検証の記録がない前提を見る」と discuss 開幕の
「この論文が確かめていないこと」も同じ序論の文（文字化け「�� ≈ 0.75」・文献キー「andre2010archetypal」を含む）で埋まった。

原因（仮説）: 負荷は依存グラフの下流到達の大きさで、そのグラフに主張の出現順の辺が入っている。IK-1001 の是正（論文単位の再計算）で値の範囲は揃ったが、
論文単位で計算すると出現順の先頭ほど大きくなる。再計算前の値が古い範囲のものだったため、［再計算］で入れ替わりが起きた（入れ替わりは IK-1001 の残り）。

## 発見の観点

生応答の欄を前後で並べ、学習者側の波及まで辿った。

## 解決の観点

未解決。出現順の辺を依存から外す（判定の正本は claim_sequence_node_ids）。

## 一般化

並びの辺と依存の辺を同じグラフに置かない。型 extent-decided-by-proxy-signal（対象の重要度を隣の手がかり — 並び順 — で決める）。

## 第 6 波の是正（2026-10-27）

オーナー判断（推奨どおり）: 依存の広がりと反実仮想の「依存」に主張の出現順の辺を含めない。`build_dependency_graph(exclude_claim_sequence_edges=True)`・判定は `claim_sequence_node_ids`。設計書 `docs/features/stakes_ledger_design.md` §16.9。

第 7 周の再演: GONE（負荷）・同根の残りは IK-1048（2026-10-28）。周の前に 4 コースの load を再計算し、Cepheus TeX で compare_claim_sequence_edges を走らせると、出現順の辺を外したときに台帳の行の多くで依存の広がりが下がり、辺ありで上位だった行はすべて下がった（r7_prereqs.md「実測」— 仮説どおり）。te-02 の［負荷再計算］の前後で合意リストの先頭（Cepheus の式 eq_tex_b214）は入れ替わらず、散布図・合意リストに「論文の主張を出現順にたどる導出の手順は、前提ではないためこの一覧に含めていません。」が出た。残り: 観測からの支持線は出現順の辺を外していない（新しい課題 IK-1048）。PDF 版の教材（GWTC の 7954db7d）の序論の文が 6be229e1 の散布図・合意リストで依存「高」に残る（te-01 seq3）理由は未確定（PDF 経路で claim チェーンの判定が効いているかは確かめていない）。run: uxsim/runs/c-r25-doubt-r7/20261027T172037Z-54876（seq3〜7）/ uxsim/runs/c-r25-doubt-r7/20261027T171356Z-51086（seq3）。
