---
id: IK-0855
title: "認識的地位台帳の component 行は理論操作グラフのノード ID（theory_op_* / eq_op_*）で作られ、学習画面の部品チップは theory_components の UUID で台帳を引くため部品の検証状態の一行は常に出ない — 同じ論文の検証状態が「わたしの地図＝間接的な支持あり（グラフのノード）」「出典タブ＝検証スコープは未記帳（主張）」「部品＝無言」の三通りになる"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/personal_map_nearby_design.md"
feature_context:
  realizing: "学生が同じ論文の主張・部品・地図の上で、それがどこまで確かめられているかを読む"
  layers: [doubt_d, personal_network, frontend_learning_ui, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [target]
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
    構造軸 representation: 台帳の target_type='component' に 2 つの名前空間が入る。`core/doubt/ledger_builder.py::_collect_targets` は
    theory_component_graphs のノード id（`_node_id` = component_id or id。実データは theory_op_0001 / eq_op_0172）で component 行を
    作り（`_backing_status` で source_backed なら indirectly_supported）、学習画面 `app.js`（2098）は部品の説明ポップアップで
    theory_components の UUID を target_id に台帳を引く。UUID の行は誰も作らないので 404 → fail-closed で一行が出ない。
    いまここの周り（`core/personal_graph/nearby.py`）はグラフのノード id で台帳を読むので「間接的な支持あり」が出る。主張は
    ledger_builder が unknown でバックフィルするので「まだ記帳されていません」。接続軸 target: 同じ論文の同じ箇所を指す 3 つの
    面が別々の対象で台帳を引く。consensus_explicit の転記も `ce.component_id::text = ANY(:ids)` がグラフのノード id と一致せず
    黙って空になる（ledger_builder.py の except の注記）。書き込み側も同じ食い違いを持つ — 台帳行の document 解決
    `core/doubt/ledger_targets.py::_LOOKUPS["component"]` と記帳の権限解決 `routes/doubt.py::_TARGET_DOCUMENT_SQL["component"]` は
    component を theory_components_live の UUID と仮定するので、グラフのノード id の対象（原稿スタジオのノード詳細から開く台帳）
    では document を解決できない（根拠の線の記帳は 404 に畳まれる見込み — 砂場では未実測）。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "同じ種別名（component）の台帳対象に、集約ノードの採番と永続行の UUID という由来の違う識別子を混在させ、画面ごとに別の名前空間で引くため、同じ箇所の状態が画面によって違う・無い"
pattern: id-namespace-conflated
discovery:
  perspective: [reproduction, trace_walk]
  note: "学生 st-02 が DCF 仮定の部品の文脈の下に検証状態の一行が無く、出典タブでは同じ仮定の主張に「未記帳」、わたしの地図では「理論の土台」に「間接的な支持あり」と出て「どれを信じればいいのか判断できない」と記した。台帳 GET の 404（component/<UUID>）と nearby の verification を ledger_builder の target_id の作り方へ辿った。 run: uxsim/runs/c-r25-doubt-r1/20261022T125655Z-43791（seq8・10・11・13）。頭脳の所見 BRAIN_NOTES_doubt.md 00054・00056。"
resolution:
  perspective: [canonical_source]
  note: >-
    「この部品の台帳対象」の解決を core/doubt/ledger_targets.py::resolve_component_ledger_target の 1 関数に置いた（台帳に (component, id) の行があればその id、theory_components の UUID なら部品の主張・式とグラフノードの linked_claim_ids / linked_equation_ids の交差でノードを選ぶ。main 層優先・交差ゼロは解決しない＝推測で結ばない）。学習者向けの台帳投影 routes/doubt.py::learner_ledger_line が通すので、部品チップ（app.js）と SA層の verification 解決器（routes/learning.py）が同じ解決に乗る。ノードへ写したときは target_note（LEDGER_COMPONENT_VIA_NODE_NOTE）で「どの段の記録か」を添え、app.js が一行に連結する。3 面の言い方の違い（検証の段階と記帳されたスコープは別軸）を manual student/02 に 1 段落足した。ledger_builder 側（UUID にも行を作る案）は採らなかった。
  landed_in:
    - "backend/core/doubt/ledger_targets.py"
    - "backend/api/routes/doubt.py"
    - "frontend/public/js/app.js"
    - "docs/manual/student/02-student.md"
    - "backend/tests/test_doubt_course_scope_round25.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "部品チップ・画面文脈・学習者 GET の 3 経路が台帳投影の 1 関数を通るので、解決も 1 箇所に置いた。"
  verification:
    methods: [guardrail]
    unverified:
      - "交差による写しが実データで期待どおりのノードを選ぶかは砂場で未確認"
      - "原稿スタジオのノード詳細から開く台帳（ノード id）の書き込み権限解決（_TARGET_DOCUMENT_SQL）は台帳行の document_id で解決されるが、行が無いノードへの初回記帳は未確認"
related: [IK-0852, IK-0412]
view_of: []
history: []
---

## 課題

症状: 学生が部品の文脈を開くと検証状態の一行が出ない。同じ論文について、出典タブは「未記帳」、わたしの地図は「間接的な
支持あり」と言う。原因: 台帳の component 行はグラフのノード id で作られ、部品チップは theory_components の UUID で引く。

## 発見の観点

ペルソナの通し受講（reproduction）で 3 つの面を続けて読み、台帳の id の作り方へ辿った（trace_walk）。

## 解決の観点

解決済み（2026-10-23）。部品 → 台帳対象の解決を 1 関数に置き、全面がそれを通す。

## 一般化

集約ノードと永続行の両方を同じ種別名で扱う台帳は、画面ごとに別の名前空間で引かれて食い違う。確かめ方は、同じ部品を
部品チップ・地図・SA層の 3 経路で引き、同じ台帳行に解決されることを見ること。

第 2 周の再演: 残存（一部）。部品チップの一行は解決器を通るようになったが、学生の部品の説明ポップアップでは承認済みの説明が無い部品で一行の取得まで進まない（IK-0906）。わたしの地図の段の札は台帳行がノード ID だけで引かれ、別の論文の記帳を出す（IK-0899）。三つの面の言い方の違いは、この二つが直るまで残る。run: uxsim/runs/c-r25-doubt-r2/20261022T221137Z-82302（seq11・13）。

第 2 波で是正（2026-10-23）: 部品の説明ポップアップは説明ゼロでも一行を取得する（IK-0906）。台帳の行キーに論文を含め（IK-0899・migration 092）、わたしの地図の札は論文ごとの行を引く（fetch_component_ledger_statuses の document_id 必須）。
