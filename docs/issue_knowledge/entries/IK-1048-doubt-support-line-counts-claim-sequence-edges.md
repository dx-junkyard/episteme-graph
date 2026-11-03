---
id: IK-1048
title: "観測からの支持線（support_paths）の計算が、依存の広がりと反実仮想から外した claim チェーン（主張の出現順の並び）の辺をまだ経路として数えるため、式どうしの依存の主張の台帳に「複数の独立した支持線に支えられています」と出て、「観測からの支持根」に序論の背景文（Dust polarization studies of RCW 57A… / Overall, Cep B exhibits a head and tail morphology…）が並ぶ"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "主張がどの観測記録に何本の独立した線で支えられているかを、台帳の事実文として教員に見せる"
  layers: [doubt_d, pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: core/doubt/support_paths.py の build_support_context は build_dependency_graph(session, document_ids=...) を既定
    （exclude_claim_sequence_edges=False）で呼び、_qualified_adjacency も graph_json の全エッジ（source_backed / partially_source_backed）を
    claim チェーン由来かどうかを見ずに採る。第 6 波（IK-1030）は負荷と反実仮想だけ辺の母集合から出現順の辺を外し、IK-1030 の
    verification.unverified に「支持線・ゼミ前ブリーフ・反証条件の入力は既定（辺を外さない）のまま」と残した。run では、出現順の辺を外した
    負荷の再計算の後も、Cepheus TeX の式どうしの依存の主張（$\ddot I$ depends on …）の台帳に「複数の独立した支持線」と序論の背景文の支持根が
    出た（ノード台帳では「単一の支持線」+ 同じ序論の文）。確定には compare_claim_sequence_edges と同型に、同じ台帳行の support_lines を
    辺あり / なしで並べる読み取りを 1 本走らせる。処理軸 logic = 読む順の辺を「観測から支える経路」として数える判定。接続軸 meaning = 並びの辺が
    別の意味（支持の流れ）で読まれる。構造・統制は設計どおり。
generalization:
  level: general
  general_form: "ある種類の辺を一つの計算から外したとき、同じグラフを読む別の計算に同じ除外を通さないと、外したはずの意味がその計算の結果に残る"
pattern: rule-enforced-per-surface
discovery:
  perspective: [trace_walk, data_inspection]
  note: "te-03 が疑義を記帳した主張の台帳（観測からの支持根の列）とノード台帳の支持線の文を、第 6 波の是正記録（IK-1030 の unverified）と support_paths.py の依存グラフの呼び出しに突き合わせた。run: uxsim/runs/c-r25-doubt-r7/20261027T171640Z-52350（seq6〜11・13・14）。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    第 6 波が負荷・反実仮想に掛けた出現順の辺の除外（オーナー判断の適用範囲を広げるだけ）を、同じ依存グラフを読む残りの
    消費者に通した。build_support_context は既定で build_dependency_graph(exclude_claim_sequence_edges=True) を作り、
    _qualified_adjacency はそのグラフが外した claim チェーン由来ノードの集合（DependencyGraph.claim_sequence_node_ids）に接する
    資格付きエッジを数えない（辺の判定は dependency.is_claim_sequence_edge 1 つ・隣接リストで再判定しない）。ゼミ前ブリーフの
    名前用グラフと反証条件の候補の入力（下流の名前）にも同じ除外を渡した。最大流・事実文・段階語は不変。先に仮説を確かめる
    読み取り compare_support_levels（純関数）/ compare_support_lines_with_and_without（1 論文・書き込みなし・段階語だけ）を置き、
    テストで run の形（背景文 2 つが出現順の辺で式の主張に届き「複数」→ 外すと観測の手順 1 本の「単一」で背景文が支持根から消える）を固定した。
  landed_in:
    - "backend/core/doubt/dependency.py"
    - "backend/core/doubt/support_paths.py"
    - "backend/core/doubt/seminar_brief.py"
    - "backend/core/doubt/falsification_conditions/input_builder.py"
    - "backend/tests/test_doubt_round25_wave7.py"
    - "docs/features/stakes_ledger_design.md"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "同じグラフを読む消費者（負荷・反実仮想・支持線・ゼミ前ブリーフ・反証条件の入力）を列挙し、辺の除外を依存グラフの組み立て 1 点に寄せた。固有の差分: 支持線は graph_json を別に読む隣接リストを持つので、判定ではなく『グラフが外した集合』を受け取らせた。"
  verification:
    methods: [guardrail]
    unverified:
      - "実データ（Cepheus TeX）で compare_support_lines_with_and_without を走らせて、式どうしの依存の主張が複数 → 単一に変わることは確かめていない（是正者は砂場 DB に触れない — 仮説は run の形を写した合成グラフで確かめた）"
      - "ゼミ前ブリーフの単一支持線の区画を画面で読み直していない（第 8 周の再演）"
related: [IK-1030, IK-0933, IK-0858]
view_of: []
history: []
---

## 課題

症状: te-03 が Cepheus TeX の式どうしの依存の主張（「In an equation of this paper, $\ddot I$ depends on $T$, $T_s$, $M$, $W$.」）の台帳を開くと、
「支持線: この対象は複数の独立した支持線に支えられています。」と「観測からの支持根:」に序論・観測節の背景文（他天体の偏光観測の一般論、
Cep B の形態の記述、^13CO の説明）が並んだ。ノード「理論の土台」の台帳では「単一の支持線」+ 同じ序論の文。依存の広がりは第 6 波の是正後の再計算済み。

原因（仮説）: 支持線の計算は依存グラフと graph_json の資格付きエッジから最大流を取るが、主張の出現順の並びの辺を外していない。第 6 波は負荷と反実仮想だけを
直し、支持線は是正の範囲外として残した。並びの先頭の背景文から式の主張まで「観測から支える経路」として届いてしまう。

## 発見の観点

台帳の支持線の欄と支持根の列を、IK-1030 の是正記録の「確かめていない範囲」と計算の呼び出し元に突き合わせた。

## 解決の観点

未解決。出現順の辺かどうかの判定を負荷・反実仮想と同じ正本（claim_sequence_node_ids）で支持線にも通す。ゼミ前ブリーフの「一点吊り」も同じ入力を読む。

## 一般化

型 rule-enforced-per-surface。同じグラフを読む計算が複数あるとき、辺の除外はグラフを作る 1 箇所（または全消費者）に掛ける。

第 8 周の再演: 是正は効いたが、名指しの主張の症状は不変（2026-10-28）。周の前の実測で Cepheus TeX の 1924 行中 98 行の段階が辺の除外で変わった（r8_prereqs.md）。一方、同期キーが名指しした主張「$\ddot I$ depends on …」の台帳は第 7 周と逐語同じ「複数の独立した支持線」+ 序論の背景文の支持根。審判が api コンテナで辺あり / なしの読み取りを 1 本走らせると、この主張は両方とも several — 「複数」は出現順の辺ではなく観測の根の同定（段による同定）と式チェーンの辺から来ていた。残る症状は IK-1057 に引き継ぐ（本エントリの是正の仮説は合成グラフでのみ確かめられていた）。run: uxsim/runs/c-r25-doubt-r8/20261027T223539Z-94356（seq5・6・14・15）。
