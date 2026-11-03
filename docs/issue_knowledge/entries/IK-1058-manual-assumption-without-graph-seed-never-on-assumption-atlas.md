---
id: IK-1058
title: "教員が手で記帳した前提（origin=manual・created_from が空）は依存の広がり（load_score）が計算されず NULL のままなので、論文に結んでスコープを記帳しても、前提の地図の散布図にも未検証合意リストにも載らない（台帳には依存の広がりの行が無く、無い理由の事実文も無い）"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-31
sources:
  - "docs/features/doubt_layer_issues.md"
feature_context:
  realizing: "教員が自分で書き起こした前提を台帳で確かめ、前提の地図と未検証合意リストの上で他の前提と並べて読む"
  layers: [doubt_d]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（コードと読み取り）: AssumptionCreateRequest は statement / reason / course_id / document_ids だけを受け、手で記帳した前提の
    created_from は {}（砂場の 4 件すべて）。load_calculator._assumption_seed_nodes は created_from の node / claim / equation の参照から
    seed を引くので seed が空になり、_load_for_seeds は None を返す。epistemic_ledger の前提の 2 行（9332f0e6・ec587be0）は［負荷再計算］の後も
    load_score NULL。get_assumption_atlas と compile_open_assumptions はどちらも `load_score IS NOT NULL` で行を絞るため、確定済み・論文に結ばれ・
    スコープ 1 件の前提 ec587be0 は、地図の点（80 件）にも合意リスト（80 件）にも現れない（api コンテナで route 関数を te-02 として読んだ）。
    構造軸 representation = 「依存の広がりが計算できない」を表す受け皿が無く NULL が「載せない」に読まれる。接続軸 information = 手で書いた前提と
    グラフ上の要素の結び付きを入力時に受け取らないので、計算に必要な情報が届かない。
generalization:
  level: general
  general_form: "計算値が無い行を一覧の絞り込み条件で落とすと、その値をそもそも計算できない種類の行（人が手で作った行）は一覧に永久に現れない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [trace_walk, data_inspection]
  note: "te-02 が確定済みの前提カードの［台帳を開く］からスコープを記帳した（8 周目で初到達）後、再読込した地図・合意リストに記帳した前提が見えなかった。頭脳は観察の上限で判定不能としたが、生応答の切り詰めを避けて route 関数と epistemic_ledger を読み取りで確かめた。run: uxsim/runs/c-r25-doubt-r8/20261027T223914Z-96688（seq7〜11）。"
resolution:
  perspective: [first_class_state]
  note: >-
    読み時の「計算できない」段（審判推奨・入力は増やさない）。前提の地図は依存の広がりが NULL の確定済みの前提を
    点にせず unplotted_points + unplotted_heading（地図の下に台帳を開くボタンで並べる）、教員の未検証合意リストは
    compile_open_assumptions(include_uncomputable_assumptions=True) で load_level="" + load_note の行として載せ、台帳 GET にも
    load_note を足した。文の正本は label_vocab.ASSUMPTION_LOAD_UNCOMPUTABLE_NOTE / ASSUMPTION_ATLAS_UNPLOTTED_HEADING（数値なし）。
    段の閾値（load_percentiles）の母集団には入れない。ゼミ前ブリーフ・discuss 開幕・学習者の一覧は既定 False で「高負荷」の意味のまま。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "backend/core/doubt/open_assumptions.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/doubt-atlas.js"
    - "backend/tests/test_doubt_round25_wave8.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "地図・合意リスト・台帳の 3 面に同じ文の正本（label_vocab）を通した。固有の差分: 一覧の意味を変えないよう、計算できない段は教員の 2 面だけに足し、他の消費者は既定のまま。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の前提 ec587be0 が地図の下・合意リストに出る見え方を画面で確かめていない（第 9 周 te-02 × t-db-assumption-review）"
      - "結ぶ要素を教員が選ぶ案（created_from に seed を持たせる）は採らなかった — 人の入力を足すため"
related: [IK-1028, IK-0853, IK-0970, IK-1030]
view_of: []
history: []
---

## 課題

症状: te-02 が前提の地図で確定済みの手の前提「磁場の乱れが小さく…」を開き（結ばれた論文: Cepheus TeX）、スコープ（条件 / 領域・根拠・理由）を記帳した。
台帳の検証状態は「未検証」、依存の広がりの行と支持線の行は出ない。再読込しても、散布図の点・未検証合意リストのどこにもこの前提は無い。
未検証合意リストの見出しは「スコープの記帳で自動的に増減します」と言うが、手で書いた前提は記帳しても増減の対象にならない。

## 発見の観点

人が書いた行と計算が作る行を、一覧の絞り込み条件（計算値の有無）に突き合わせた。

## 解決の観点

未解決（上の resolution.note）。

## 一般化

型 information-dropped-as-unrepresentable。計算値で一覧を絞るときは、その値を持てない行の種類を列挙し、受け皿（計算できない旨の段）を用意する。

第 8 波の是正（2026-10-31）: 読み時の「計算できない」段で地図の下・合意リスト・台帳に事実文で出す（上の resolution）。

第 9 周の再演（2026-10-31・固定コース 1bad7d9f）: 前提の地図の下に「依存の広がりを計算できない前提」区画が出て、手の前提 3 件が並ぶ（GONE — 黙って消えない）。未検証合意リストは観察の上限で末尾が切れ、画面では確かめられなかった（ハーネス H53）が、審判の読み取り（砂場 api コンテナ・書き込みなし）で 1bad7d9f の open-assumptions 83 行の 81〜83 行目に同じ 3 件が load_note 付きで載ることを確認した。前提 → 台帳 → スコープの区間は、ハーネスが区画のボタンを開けず（H52）9 周目も画面では未到達。地図の行が被覆の段・同文の区別を描かない件は新規 IK-1074。結び無しの旧い前提 2 件（document なし）はどのコースの範囲にも入らず地図に出ない（コースの範囲の規則どおり・記帳しない）。run: uxsim/runs/c-r25-doubt-r9/20261031T003210Z-30688（seq3・8・9・10）。
