---
id: IK-0932
title: "学習者の「わたしの地図」が、教員がスコープ 1 件で記帳した主グラフのノードを「直接検証の記帳あり」とだけ示し、成立条件・領域を出さず、学習者がノードの台帳（スコープ）へ辿る面も無いため、条件付きの検証が無条件に読め、IK-0907 で AI に渡した「条件は出典タブの台帳の一行に出る」もノードの記帳には当たらない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/features/personal_map_nearby_design.md"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/assistant_screen_adapter_design.md"
feature_context:
  realizing: "学習者が自分の地図の検証の札から、どの条件で確かめられたかへ 1 操作で届く"
  layers: [personal_network, doubt_d, screen_adapter_sa, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information, meaning]
    governance: [none]
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
    接続軸 information: personal_graph/queries.fetch_component_ledger_statuses は verification_status だけを読み（scope は読まない）、nearby._verification は段階ラベルだけを
    DTO に載せる。D層の不変条項は「検証を単一ブールにしない・directly_verified はスコープ 1 件以上」で、地図はスコープを落として状態だけを運ぶ。学習者の出典タブ
    （app.js 3335 行付近）は出典チャンクの主張・式だけの台帳の一行を描き、ノード（component）の行は出さないので、学習者がノードのスコープに届く面が無い。接続軸
    meaning: schema.LEARNING_PERSONAL_MAP_SCREEN_FACT は「条件は出典タブにある台帳の一行に出ます」と AI に渡すが、ノードの記帳はそこに出ないので画面の事実と食い違い、
    AI は日本語で「担当の先生に尋ねる」、英語で「地図の札を見よ」と案内した。処理軸 none。構造軸 none（台帳はスコープを持つ）。
generalization:
  level: repo_pattern
  general_form: "条件付きの記録を状態のラベルだけに縮めて別の画面へ運び、条件を読む道をその画面に置かないため、受け手には無条件の主張として読める"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [invariant_audit, reproduction]
  note: >-
    te-02 が Cepheus の Theory basis にスコープ 1 件（DCF の等分配の仮定が成り立つ範囲・分子雲の偏光観測）で直接検証を記帳した後、st-01 と st-04 の地図は
    「理論の土台［直接検証の記帳あり］」だけを出し、AI に条件を尋ねても答えに届かなかった。run: uxsim/runs/c-r25-doubt-r3/20261023T033238Z-24957（seq8・9）/
    uxsim/runs/c-r25-doubt-r3/20261023T033451Z-40732（seq14・15）/ uxsim/runs/c-r25-doubt-r3/20261023T070217Z-13811（seq14・15）。
resolution:
  perspective: [carry_through]
  note: >-
    学習者の地図の札に、状態が直接検証・間接的な支持・反証のときだけ「記帳された条件: 成立条件「…」・領域「…」」の 1 行を添える（core/doubt/schema.py learner_scope_line。根拠の逐語・記帳者・精度・系・数値は出さない。条件が 3 つ以上なら件数を言わずに「ほかにも条件の記帳があります」）。personal_graph/queries.fetch_component_ledger_statuses が verification_scopes も読み、状態の dict の形を変えずに ComponentLedgerStatuses.scope_lines で運び、nearby._verification が verification.scope_line に載せる。personal-map-home.js は札の下に一覧で描く（文言はサーバ）。SA層の画面の事実（LEARNING_PERSONAL_MAP_SCREEN_FACT）を「札の下に条件が 1 行で出る・根拠の引用・記帳した人・精度は出ない」に合わせた。オーナー判断の候補は推奨どおり（stakes_ledger_design.md §16 追補）。札から学習者向け台帳 GET を開く展開（代案）は置いていない。
  landed_in:
    - "backend/core/doubt/schema.py"
    - "backend/core/personal_graph/queries.py"
    - "backend/core/personal_graph/nearby.py"
    - "backend/core/assistant_context/schema.py"
    - "frontend/public/js/personal-map-home.js"
    - "frontend/public/css/styles.css"
    - "frontend/public/index.html"
    - "docs/features/stakes_ledger_design.md"
    - "backend/tests/test_doubt_round25_wave3.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実画面のわたしの地図で条件の 1 行が札の下に読めること"
      - "その画面で学生が AI に条件を尋ねたときの答え"
related: [IK-0907, IK-0855, IK-0899]
view_of: []
history: []
---

## 課題

症状: 学習者の地図で「理論の土台」だけが「直接検証の記帳あり」になり、どの条件で確かめたかはどこにも出ない。AI に尋ねると、
日本語では「記帳の中身は渡されていないので先生に」、英語では「地図の札を見てください」と答え、学習者は地図と AI を往復して止まる。

原因: 地図は台帳の状態だけを読み、スコープを運ばない。学習者の出典タブは主張・式の一行だけで、ノードの台帳を出さない。AI に渡す
画面の事実（IK-0907 の是正）は出典タブを行き先に挙げるが、ノードの記帳はそこに無い。

## 発見の観点

教員の記帳の直後に学生 2 人に地図 → AI の順で条件を探させ（再現）、D層の不変条項（検証を単一ブールにしない）と地図の DTO を突き合わせた。

## 解決の観点

条件付きの記録を条件ごと運び（carry_through）、地図の札の下に条件の 1 行を出して、AI の事実文をその画面に合わせた。（第 25 周 第 3 波・2026-10-23）

## 一般化

検証・承認のように条件付きでしか意味を持たない記録を、要約のラベルにして別の画面へ渡すと、受け手は無条件に読む。確かめ方は、
条件付きの記録を 1 件作り、受け手の画面から 1 操作で条件の文に届くことを見ること。
