---
id: IK-0969
title: "閲覧だけの論文の台帳でも［AIに候補を出してもらう］（覆る条件の候補）だけは無効にならず、押すと台帳の対象 1 件ではなく前提の地図タブで選んでいるコース全体に LLM の候補生成を予約する — 記帳ボタンの無効化（IK-0900）の対象から漏れ、操作の範囲も画面に出ない"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "閲覧だけの教員が他の教員の論文の台帳を開き、自分にできる操作とできない操作を見分ける"
  layers: [doubt_d, stakes_ledger_sl, frontend_admin_ui, lecture_studio]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition, target]
    governance: [assignment]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 condition: doubt-atlas.js disableLedgerWrites の選択子は scope / vstatus / candidate / falsification-form / falsification-confirm・dismiss /
    evidence-line-form を含むが data-doubt-falsification-refresh を含まない（can_edit=false でもボタンが生きる）。接続軸 target: bindFalsificationRefresh は
    台帳の対象ではなく tabState.courseId の POST /courses/{id}/falsification-candidates/refresh を呼び、サーバはコースの教材全体に非同期の LLM 候補生成を予約する
    （確定は _require_editable_ledger_target で閉じるので、閲覧だけの教員が作った候補を自分では確定できない）。統制軸 assignment: 台帳の 1 対象の画面に、
    コース単位の操作の責任が置かれている。構造軸 none（データの形の問題ではない）。処理軸 none。
generalization:
  level: repo_pattern
  general_form: "書き込み系の操作を一括で無効にする選択子が、後から足された操作を取りこぼし、しかもその操作の範囲が置かれた画面の対象より広い"
pattern: permission-and-affordance-asymmetric
discovery:
  perspective: [boundary_walk, symptom_report]
  note: >-
    閲覧だけの PINN の台帳を te-01 が 2 つのノードで開き、記帳できない理由の事実文と［スコープを記帳］［検証状態を記帳する］の無効化は読めたが、
    ［AIに候補を出してもらう］ほかが押せるかどうか迷った。製品の選択子と照合すると、覆る条件の記帳・根拠の線は無効化される（投影の印の欠け = ハーネス）が、
    候補の予約だけは生きていた。run: uxsim/runs/c-r25-doubt-r4/20261024T232812Z-98847（seq5・6）。
resolution:
  perspective: [fail_closed, explicit_contract]
  note: >-
    disableLedgerWrites の選択子に data-doubt-falsification-refresh と data-doubt-proposal-status を足し、閲覧だけの台帳では AI 候補の予約と提案の状態遷移も押せなくした。台帳 GET は falsification_refresh_note（予約の範囲はコースの教材全体）を返し、ボタンの下に出す（範囲を言う）。コース単位の予約を前提の地図のバッチ区画へ移す案は採らなかった（台帳から辿る教員の動線を切らない）。
  landed_in:
    - "frontend/public/js/doubt-atlas.js"
    - "backend/core/doubt/scope.py"
    - "backend/api/routes/doubt.py"
    - "docs/manual/teacher/18-admin-doubt-atlas.md"
  verification:
    methods: [guardrail]
    unverified:
      - "閲覧だけの教員で全ボタンを列挙して押せないことは画面で未確認"
      - "サーバの refresh エンドポイントのコース単位ゲートは変えていない（_course_scope_document_ids のまま）"
related: [IK-0900, IK-0931]
view_of: []
history: []
---

## 課題

症状: 閲覧だけの論文の台帳で、記帳ボタンは無効になるのに［AIに候補を出してもらう］は押せる。押すと台帳の対象ではなく、別のタブで選んでいるコース全体の候補生成が予約される。

原因: 書き込みを無効にする選択子にこのボタンが入っていない。ボタンの実体はコース単位のバッチで、台帳の 1 対象の画面に置かれている。

## 発見の観点

権限の無い教員で台帳を開き（境界の歩行）、画面に見える操作と実行される要求の範囲を突き合わせた。

## 解決の観点

第 4 波で是正（2026-10-25）。無効化の選択子への追加と、操作の範囲を画面の対象に合わせる（または範囲を言う）。

## 一般化

「書き込み系を一括で止める」一覧は、後から足した操作を取りこぼす。確かめ方は、権限の無い利用者で画面の全ボタンを列挙し、生きている各ボタンの要求先と範囲を確かめること。

第 5 周の再演: GONE（2026-10-27）。te-02（PINN を閲覧だけ）の台帳で［覆る条件を記帳する］［AIに候補を出してもらう］を含む全記帳ボタンに押せない印が付き、［AIに候補を出してもらう］の範囲（コースの教材全体）の事実文も出た。run: uxsim/runs/c-r25-doubt-r5/20261027T070623Z-23504（seq5〜7）。
