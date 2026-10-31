---
id: IK-1000
title: "前提の地図タブのコース選択は /api/admin/courses の先頭（更新の新しい順）を既定にし、本人の所有か共有かを見ないため、共有を受けた同題名のコースが更新されるたびに本人のコースが既定から外れ、本人が確定した前提も、記帳した論文のコースも見えないまま「該当なし」になる"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/stakes_ledger_design.md §16.8"
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
feature_context:
  realizing: "教員が前提の地図タブを開き、自分のコースの前提と未検証の合意を確かめる"
  layers: [doubt_d, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 接続軸 target — doubt-atlas.js loadCourses は selectedIndex=1（一覧の先頭）を既定にし、一覧は更新順。te-03 の一覧は共有（編集可）の 6be229e1 が先頭・自分の 74ca9b48 が 2 番目で、既定は 6be229e1。6be229e1 の updated_at は同時刻に走る他の面のレーンで 07:14 に 2 度更新されていた（並びは他人の操作で変わる）。te-04 も同題名 2 コースの先頭（6be229e1）が選ばれ、自分が疑義を残した Cepheus（TeX 版）はそのコースに無いので地図に出なかった。確定には、周の前に置いた手動の前提（confirmed）が 74ca9b48 に結ばれていることを 74ca9b48 の assumptions?status=confirmed で見ることが要る（今周の run は 74ca9b48 を開いていない）。同題名の判別不能自体は IK-0407。
generalization:
  level: repo_pattern
  general_form: "選択の既定を一覧の並び（他人の操作で変わる順）の先頭に置き、利用者自身のものを優先しないため、見たい対象が黙って別の対象に置き換わる"
pattern: default-hides-choice
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    te-03 の前提の地図は既定で共有のコース 6be229e1 を開き、確定済みに切り替えても「該当する前提はありません」だった（自分の 74ca9b48 は選択肢の 2 番目）。te-04 も 6be229e1 を開き、疑義を残した Cepheus の主張が地図・合意リストに無かった。run: uxsim/runs/c-r25-doubt-r5/20261027T071425Z-60143（seq3・7・10）/ uxsim/runs/c-r25-doubt-r5/20261027T070946Z-33422（seq12）。
resolution:
  perspective: [carry_through]
  note: >-
    doubt-atlas.js の loadCourses が既定のコースを role=owner の先頭にし、無ければ従来どおり一覧の先頭。選択肢に役割（サーバの role_label）を併記する。前回の選択を端末に覚える案は入れていない。
  landed_in:
    - "frontend/public/js/doubt-atlas.js"
    - "frontend/public/admin.html"
    - "docs/manual/teacher/18-admin-doubt-atlas.md"
    - "backend/tests/test_doubt_round25_wave5.py"
  verification:
    methods: [guardrail]
    unverified:
      - "共有元の更新後に既定が変わらないことを実機で確かめていない"
      - "仮説の残り（手動の前提が本人のコースに結ばれていたか）は照会していない"
related: [IK-0407, IK-0970, IK-0853]
view_of: []
history: []
---

## 課題

症状: 前提の地図を開くと他人のコースが選ばれていて、本人が確定した前提・疑義を残した論文が地図にも合意リストにも出ない。画面は「該当なし」で、選ばれたコースが違うことにも、そのコースに論文が入っていないことにも気づけない。

原因（仮説）: 既定のコースを一覧の先頭（更新順）で選び、所有・共有を見ない。

## 発見の観点

再現（te-03・te-04 の前提の地図）と、各 run の /api/admin/courses の並びと updated_at の時系列の照合。

## 解決の観点

第 25 周 面 B 第 5 波（2026-10-27・docs/features/stakes_ledger_design.md §16.8）で解決。doubt-atlas.js の loadCourses が既定のコースを role=owner の先頭にし、無ければ従来どおり一覧の先頭。選択肢に役割（サーバの role_label）を併記する。前回の選択を端末に覚える案は入れていない。

起票時の是正案: 是正案: 既定は ①前回このタブで選んだコース（端末の localStorage・fail-soft）②本人所有のコース ③先頭 の順。選択肢に役割（所有 / 共有（編集可））を併記する（コース一覧の role_label の正本を使う）。地図の見出しに「このコースの教材: 論文A・論文B」を 1 行出し、台帳から来た論文がコースに含まれないことを読めるようにする。

## 一般化

並びが他人の操作で変わる一覧の先頭を既定にすると、利用者の見ている対象が黙って入れ替わる。確かめ方は、共有を受けた教員で、共有元を更新した後にタブを開き直して既定が変わらないことを見ること。

第 6 周の再演: GONE（2026-10-27）。前提の地図の既定は 3 人とも本人所有のコース（te-05 は 7dcbf662・te-01 は 6be229e1・te-04 は 1bad7d9f — te-04 は course_hint 付き）。残り: te-05 の選択肢に同題名の共有コースが 2 つ同じ役割の括弧で並び見分けられない（IK-0407）。run: uxsim/runs/c-r25-doubt-r6/20261027T122225Z-3508（seq3）/ uxsim/runs/c-r25-doubt-r6/20261027T122508Z-5219（seq3）。
